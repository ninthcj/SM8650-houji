// SPDX-License-Identifier: GPL-2.0-only
/*
 * Xiaomi SM8650 (houji) BlackBox Minidump kmsg bridge ("md_kmsg") for GKI 2.0 6.12.
 *
 * On SM8650 under Gunyah EL2 (pineapple-oemvm.dtsi push-compatible = "qcom,minidump_rm"),
 * Gunyah Resource Manager owns the HLOS Minidump TOC. GKI 6.12 has no debug_kinfo, so
 * minidump.ko defers forever on debug_symbol_available() and never drains pending_list.
 *
 * This module registers "md_kmsg" directly with Gunyah RM via gh_rm_minidump_register_range()
 * (as well as msm_minidump_add_region() and bare-metal SMEM 602 fallback when Gunyah RM is
 * absent) so Xiaomi XBLRamDump_BlackBox and ABL 'fastboot oem lkmsg' always find "md_kmsg".
 */

#define pr_fmt(fmt) "mi_md_kmsg: " fmt

#include <linux/arm-smccc.h>
#include <linux/delay.h>
#include <linux/firmware/qcom/qcom_scm.h>
#include <linux/gunyah/gh_rm_drv.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kmsg_dump.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/reboot.h>
#include <linux/sizes.h>
#include <linux/slab.h>
#include <linux/soc/qcom/smem.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>
#include <soc/qcom/minidump.h>
#include <soc/qcom/watchdog.h>
#include <asm/barrier.h>

#define MI_MD_KMSG_SIZE		SZ_256K
#define MI_MD_SYNC_MS		200
#define MI_MD_SYNC_ITERS	1500	/* 300s of early/stage-2 boot coverage */
#define MI_ARM_CLEAR_ITERS	450	/* 90s: clear one-shot fastboot once Android is up */

#define MI_PON_REASON_NORMAL		0x00
#define MI_PON_REASON_BOOTLOADER	0x02

/* Gunyah EL2 virtual watchdog SMCCC hypercalls (stateless, zero DT probe dependency) */
#define MI_GH_VIRT_WDT_CONTROL \
	ARM_SMCCC_CALL_VAL(ARM_SMCCC_FAST_CALL, ARM_SMCCC_SMC_32, \
			   ARM_SMCCC_OWNER_VENDOR_HYP, 0x0005)
#define MI_GH_VIRT_WDT_PET \
	ARM_SMCCC_CALL_VAL(ARM_SMCCC_FAST_CALL, ARM_SMCCC_SMC_32, \
			   ARM_SMCCC_OWNER_VENDOR_HYP, 0x0007)
#define MI_GH_VIRT_WDT_SET_TIME \
	ARM_SMCCC_CALL_VAL(ARM_SMCCC_FAST_CALL, ARM_SMCCC_SMC_32, \
			   ARM_SMCCC_OWNER_VENDOR_HYP, 0x0008)

/* Qualcomm SMEM 602 Minidump TOC ABI (bare-metal fallback when Gunyah RM is not active) */
#define MI_SBL_MINIDUMP_SMEM_ID	602
#define MI_MAX_NUM_OF_SS	10
#define MI_MD_SS_HLOS_ID	0
#define MI_MAX_REG_NAME_LEN	16

#define MI_MD_REGION_VALID	(('V' << 24) | ('A' << 16) | ('L' << 8) | 'I')
#define MI_MD_SS_ENCR_NOTREQ	(('N' << 8) | 'R')
#define MI_MD_SS_ENCR_DONE	(('D' << 24) | ('O' << 16) | ('N' << 8) | 'E')
#define MI_MD_SS_ENABLED	(('E' << 24) | ('N' << 16) | ('B' << 8) | 'L')

struct mi_md_ss_region {
	char	name[MI_MAX_REG_NAME_LEN];
	u32	seq_num;
	u32	md_valid;
	u64	region_base_address;
	u64	region_size;
};

struct mi_md_ss_toc {
	u32	md_ss_toc_init;
	u32	md_ss_enable_status;
	u32	encryption_status;
	u32	encryption_required;
	u32	ss_region_count;
	u64	md_ss_smem_regions_baseptr;
};

struct mi_md_global_toc {
	u32			md_toc_init;
	u32			md_revision;
	u32			md_enable_status;
	struct mi_md_ss_toc	md_ss_toc[MI_MAX_NUM_OF_SS];
};

static char *mi_kmsg_buf;
static struct mi_md_ss_region *mi_direct_regions;
static struct md_region mi_md_entry;
static struct nvmem_cell *mi_pon_cell;
static DEFINE_RAW_SPINLOCK(mi_md_lock);
static int mi_gh_slot = -1;
static bool mi_pon_fastboot_armed;
static bool mi_dload_armed;
static bool mi_in_crash;
static bool mi_reboot_notified;
static unsigned int mi_sync_count;
static struct delayed_work mi_sync_dwork;
static unsigned int boot_guard_sec;
module_param(boot_guard_sec, uint, 0644);
MODULE_PARM_DESC(boot_guard_sec, "Dead-man switch seconds before auto-fastboot unless disarmed (0=disabled)");

static void mi_set_dload_mode(enum qcom_download_mode mode)
{
	if (!qcom_scm_is_available())
		return;
	qcom_scm_set_download_mode(mode);
	WRITE_ONCE(mi_dload_armed, mode == QCOM_DOWNLOAD_MINIDUMP);
}

static inline void mi_flush_dcache_poc(const void *addr, size_t len)
{
	uintptr_t cur = (uintptr_t)addr & ~(uintptr_t)63;
	uintptr_t end = ALIGN((uintptr_t)addr + len, 64);

	for (; cur < end; cur += 64)
		asm volatile("dc civac, %0" :: "r"(cur) : "memory");
	dsb(sy);
}

/* Process context only: arm/disarm PMIC SDAM restart_reason -> 0x02 (bootloader) so 1 failed boot goes straight to Fastboot. */
static void mi_update_pon_reason(bool arm_bootloader)
{
	u8 val = arm_bootloader ? MI_PON_REASON_BOOTLOADER : MI_PON_REASON_NORMAL;

	if (!mi_pon_cell) {
		struct device_node *np;

		np = of_find_compatible_node(NULL, NULL, "qcom,reboot-reason");
		if (!np)
			return;
		mi_pon_cell = of_nvmem_cell_get(np, "restart_reason");
		of_node_put(np);
		if (IS_ERR(mi_pon_cell)) {
			mi_pon_cell = NULL;
			return;
		}
	}

	if (mi_pon_fastboot_armed == arm_bootloader)
		return;

	if (nvmem_cell_write(mi_pon_cell, &val, sizeof(val)) >= 0) {
		mi_pon_fastboot_armed = arm_bootloader;
		pr_info("PMIC SDAM restart_reason %s (0x%02x)\n",
			arm_bootloader ? "armed -> one-shot fastboot on crash" : "disarmed -> normal",
			val);
	}
}

/* Must only be called in process context (init or workqueue). */
static void mi_try_register_gh_rm(void)
{
	int slot;

	if (READ_ONCE(mi_gh_slot) >= 0 || READ_ONCE(mi_gh_slot) == -EOPNOTSUPP ||
	    !mi_kmsg_buf)
		return;

	slot = gh_rm_minidump_register_range(mi_md_entry.phys_addr,
					     MI_MD_KMSG_SIZE,
					     "md_kmsg", strlen("md_kmsg"));
	if (slot >= 0) {
		WRITE_ONCE(mi_gh_slot, slot);
		pr_info("registered md_kmsg in Gunyah RM minidump slot %d (phys %pa)\n",
			slot, &mi_md_entry.phys_addr);
	} else if (slot != -EAGAIN && slot != -ENODEV && slot != -EPROBE_DEFER) {
		WRITE_ONCE(mi_gh_slot, -EOPNOTSUPP);
	}
}

static void mi_ensure_smem_toc(void)
{
	struct mi_md_global_toc *gbl;
	struct mi_md_ss_toc *ss;
	size_t sz = 0;

	if (READ_ONCE(mi_gh_slot) >= 0 || !mi_direct_regions || !mi_kmsg_buf)
		return;

	gbl = qcom_smem_get(QCOM_SMEM_HOST_ANY, MI_SBL_MINIDUMP_SMEM_ID, &sz);
	if (IS_ERR_OR_NULL(gbl) || sz < sizeof(*gbl) || !gbl->md_toc_init)
		return;

	ss = &gbl->md_ss_toc[MI_MD_SS_HLOS_ID];
	if (READ_ONCE(ss->md_ss_enable_status) == MI_MD_SS_ENABLED &&
	    READ_ONCE(ss->ss_region_count) > 0 &&
	    READ_ONCE(ss->md_ss_smem_regions_baseptr) != 0)
		return;

	memset(mi_direct_regions, 0, sizeof(*mi_direct_regions) * 2);
	strscpy(mi_direct_regions[0].name, "md_kmsg",
		sizeof(mi_direct_regions[0].name));
	mi_direct_regions[0].seq_num = 0;
	mi_direct_regions[0].region_base_address = mi_md_entry.phys_addr;
	mi_direct_regions[0].region_size = MI_MD_KMSG_SIZE;
	mi_direct_regions[0].md_valid = MI_MD_REGION_VALID;
	mi_flush_dcache_poc(mi_direct_regions, sizeof(*mi_direct_regions) * 2);

	ss->encryption_status = MI_MD_SS_ENCR_DONE;
	ss->encryption_required = MI_MD_SS_ENCR_NOTREQ;
	ss->md_ss_smem_regions_baseptr = virt_to_phys(mi_direct_regions);
	ss->ss_region_count = 1;
	ss->md_ss_toc_init = 1;
	dsb(sy);
	WRITE_ONCE(ss->md_ss_enable_status, MI_MD_SS_ENABLED);
	dsb(sy);
}

static void mi_snapshot_kmsg(bool force_unlocked)
{
	struct kmsg_dump_iter iter;
	unsigned long flags = 0;
	bool locked = false;
	size_t len = 0;

	if (!mi_kmsg_buf)
		return;

	if (raw_spin_trylock_irqsave(&mi_md_lock, flags))
		locked = true;
	else if (!force_unlocked)
		return;

	kmsg_dump_rewind(&iter);
	if (kmsg_dump_get_buffer(&iter, false, mi_kmsg_buf,
				 MI_MD_KMSG_SIZE - 1, &len)) {
		if (len < MI_MD_KMSG_SIZE)
			memset(mi_kmsg_buf + len, 0, MI_MD_KMSG_SIZE - len);
		mi_flush_dcache_poc(mi_kmsg_buf, MI_MD_KMSG_SIZE);
	}
	mi_ensure_smem_toc();

	if (locked)
		raw_spin_unlock_irqrestore(&mi_md_lock, flags);
}

static void mi_md_kmsg_dump(struct kmsg_dumper *dumper,
			    struct kmsg_dump_detail *detail)
{
	bool crash = (detail->reason == KMSG_DUMP_PANIC ||
		      detail->reason == KMSG_DUMP_OOPS ||
		      detail->reason == KMSG_DUMP_EMERG);

	if (crash) {
		WRITE_ONCE(mi_in_crash, true);
		mi_set_dload_mode(QCOM_DOWNLOAD_MINIDUMP);
		reboot_mode = REBOOT_WARM;
	}

	mi_snapshot_kmsg(detail->reason == KMSG_DUMP_PANIC);
}

static struct kmsg_dumper mi_kmsg_dumper = {
	.dump = mi_md_kmsg_dump,
	.max_reason = KMSG_DUMP_MAX,
};

static bool mi_is_intentional_non_bootloader_target(const char *cmd)
{
	if (!cmd || !cmd[0])
		return false;
	return !strcmp(cmd, "recovery") ||
	       !strcmp(cmd, "fastboot") ||
	       !strcmp(cmd, "userrequested") ||
	       !strcmp(cmd, "ota") ||
	       !strcmp(cmd, "edl");
}

static void mi_trigger_wdt_bite(bool can_register_gh)
{
	struct arm_smccc_res res = {0};
	struct arm_smccc_res ctl_res = {0};

	if (can_register_gh)
		mi_try_register_gh_rm();
	mi_set_dload_mode(QCOM_DOWNLOAD_MINIDUMP);
	reboot_mode = REBOOT_WARM;
	mi_snapshot_kmsg(true);

	qcom_wdt_trigger_bite();

	/* Direct Gunyah EL2 virtual watchdog bite: bark (1ms) MUST be < bite (20ms)! */
	arm_smccc_1_1_smc(MI_GH_VIRT_WDT_CONTROL, 3, &ctl_res);
	arm_smccc_1_1_smc(MI_GH_VIRT_WDT_SET_TIME, 1, 20, &res);
	if (res.a0 != 0)
		arm_smccc_1_1_smc(MI_GH_VIRT_WDT_SET_TIME, 0xFFFF, 20, &res);
	arm_smccc_1_1_smc(MI_GH_VIRT_WDT_PET, 0, &ctl_res);

	pr_emerg("EL2 vWDOG armed (set_time a0=0x%lx), waiting for bite...\n", res.a0);
	mi_snapshot_kmsg(true);

	if (res.a0 != (unsigned long)-1) {
		mdelay(12000);
		while (1)
			udelay(1);
	}
}

/* Priority 300 runs ahead of qcom-reboot-reason (255), so clean 'reboot recovery/bootloader' still overrides 0x00. */
static int mi_md_reboot_cb(struct notifier_block *nb,
			   unsigned long action, void *data)
{
	const char *cmd = (action == SYS_RESTART && data) ? (const char *)data : NULL;
	unsigned int guard = READ_ONCE(boot_guard_sec);

	if (READ_ONCE(mi_in_crash)) {
		mi_update_pon_reason(true);
		mi_trigger_wdt_bite(true);
	} else {
		WRITE_ONCE(mi_reboot_notified, true);
		cancel_delayed_work_sync(&mi_sync_dwork);
		if (cmd && !strcmp(cmd, "bootloader_clean")) {
			mi_update_pon_reason(true);
			mi_set_dload_mode(QCOM_DOWNLOAD_NODUMP);
		} else if (cmd && !strcmp(cmd, "wdt_bite")) {
			pr_emerg("wdt_bite requested -> snapshotting md_kmsg & triggering Gunyah EL2 WDT bite\n");
			mi_update_pon_reason(true);
			mi_trigger_wdt_bite(true);
		} else if (cmd && !strcmp(cmd, "bootloader")) {
			mi_update_pon_reason(true);
			if (guard > 0 && READ_ONCE(mi_sync_count) < MI_ARM_CLEAR_ITERS) {
				pr_emerg("early-boot reboot bootloader (InitFatalReboot) at iter=%u -> dumping md_kmsg via EL2 WDT bite\n",
					 READ_ONCE(mi_sync_count));
				mi_trigger_wdt_bite(true);
			} else {
				mi_set_dload_mode(QCOM_DOWNLOAD_NODUMP);
			}
		} else if (guard > 0 && action == SYS_RESTART &&
			   READ_ONCE(mi_sync_count) < MI_ARM_CLEAR_ITERS &&
			   !mi_is_intentional_non_bootloader_target(cmd)) {
			pr_emerg("early-boot restart (%s) at iter=%u -> forcing 1-shot fastboot + md_kmsg\n",
				 cmd ? cmd : "<null>", READ_ONCE(mi_sync_count));
			mi_update_pon_reason(true);
			mi_trigger_wdt_bite(true);
		} else {
			mi_update_pon_reason(false);
			mi_set_dload_mode(QCOM_DOWNLOAD_NODUMP);
		}
	}
	return NOTIFY_DONE;
}

static struct notifier_block mi_reboot_nb = {
	.notifier_call = mi_md_reboot_cb,
	.priority = 300,
};

/* Runs right before qcom_scm_do_restart (130) & psci_sys_reset (129) on panic/emergency_restart */
static int mi_md_restart_cb(struct notifier_block *nb,
			    unsigned long action, void *data)
{
	if (READ_ONCE(mi_in_crash)) {
		pr_info("Triggering late EL2 WDT bite\n");
		mi_trigger_wdt_bite(false);
	}
	return NOTIFY_DONE;
}

static struct notifier_block mi_restart_nb = {
	.notifier_call = mi_md_restart_cb,
	.priority = 200,
};

static void mi_sync_work_fn(struct work_struct *work)
{
	unsigned int guard = READ_ONCE(boot_guard_sec);

	if (READ_ONCE(mi_reboot_notified))
		return;

	mi_try_register_gh_rm();
	if (guard == 0) {
		if (mi_pon_fastboot_armed && !READ_ONCE(mi_in_crash))
			mi_update_pon_reason(false);
		++mi_sync_count;
		if (!READ_ONCE(mi_in_crash) && !READ_ONCE(mi_reboot_notified) &&
		    READ_ONCE(mi_gh_slot) < 0 && mi_sync_count < 25)
			schedule_delayed_work(&mi_sync_dwork,
					      msecs_to_jiffies(MI_MD_SYNC_MS));
		return;
	}
	if (!READ_ONCE(mi_in_crash) && !READ_ONCE(mi_reboot_notified)) {
		if (!READ_ONCE(mi_dload_armed))
			mi_set_dload_mode(QCOM_DOWNLOAD_MINIDUMP);
		mi_update_pon_reason(true);
	}
	mi_snapshot_kmsg(false);
	if (!READ_ONCE(mi_reboot_notified) &&
	    mi_sync_count >= guard * (1000 / MI_MD_SYNC_MS)) {
		pr_emerg("boot_guard_sec=%u expired without disarm -> forcing 1-shot fastboot + md_kmsg\n",
			 guard);
		mi_update_pon_reason(true);
		mi_trigger_wdt_bite(true);
		return;
	}
	++mi_sync_count;
	if (!READ_ONCE(mi_in_crash) && !READ_ONCE(mi_reboot_notified) &&
	    mi_sync_count < MI_MD_SYNC_ITERS)
		schedule_delayed_work(&mi_sync_dwork,
				      msecs_to_jiffies(MI_MD_SYNC_MS));
}

static int __init mi_md_kmsg_init(void)
{
	unsigned int guard = READ_ONCE(boot_guard_sec);
	int ret;

	mi_kmsg_buf = alloc_pages_exact(MI_MD_KMSG_SIZE, GFP_KERNEL | __GFP_ZERO);
	if (!mi_kmsg_buf)
		return -ENOMEM;

	mi_direct_regions = kzalloc(sizeof(*mi_direct_regions) * 2, GFP_KERNEL);
	if (!mi_direct_regions) {
		free_pages_exact(mi_kmsg_buf, MI_MD_KMSG_SIZE);
		mi_kmsg_buf = NULL;
		return -ENOMEM;
	}

	memset(&mi_md_entry, 0, sizeof(mi_md_entry));
	strscpy(mi_md_entry.name, "md_kmsg", sizeof(mi_md_entry.name));
	mi_md_entry.virt_addr = (uintptr_t)mi_kmsg_buf;
	mi_md_entry.phys_addr = virt_to_phys(mi_kmsg_buf);
	mi_md_entry.size = MI_MD_KMSG_SIZE;

	mi_try_register_gh_rm();

	ret = msm_minidump_add_region(&mi_md_entry);
	if (ret < 0)
		pr_warn("msm_minidump_add_region returned %d\n", ret);

	ret = kmsg_dump_register(&mi_kmsg_dumper);
	if (ret)
		pr_warn("kmsg_dump_register returned %d\n", ret);

	register_reboot_notifier(&mi_reboot_nb);
	register_restart_handler(&mi_restart_nb);
	INIT_DELAYED_WORK(&mi_sync_dwork, mi_sync_work_fn);
	if (guard > 0) {
		mi_update_pon_reason(true);
		mi_snapshot_kmsg(false);
		mi_set_dload_mode(QCOM_DOWNLOAD_MINIDUMP);
		schedule_delayed_work(&mi_sync_dwork,
				      msecs_to_jiffies(MI_MD_SYNC_MS));
	} else if (READ_ONCE(mi_gh_slot) < 0) {
		schedule_delayed_work(&mi_sync_dwork,
				      msecs_to_jiffies(MI_MD_SYNC_MS));
	}

	pr_info("armed md_kmsg (%u bytes @ phys %pa, gh_slot=%d, pon_armed=%d, guard=%u)\n",
		(unsigned int)MI_MD_KMSG_SIZE, &mi_md_entry.phys_addr,
		READ_ONCE(mi_gh_slot), mi_pon_fastboot_armed, guard);
	return 0;
}

static void __exit mi_md_kmsg_exit(void)
{
	int slot = READ_ONCE(mi_gh_slot);

	cancel_delayed_work_sync(&mi_sync_dwork);
	mi_update_pon_reason(false);
	if (mi_pon_cell) {
		nvmem_cell_put(mi_pon_cell);
		mi_pon_cell = NULL;
	}
	unregister_restart_handler(&mi_restart_nb);
	unregister_reboot_notifier(&mi_reboot_nb);
	kmsg_dump_unregister(&mi_kmsg_dumper);
	if (slot >= 0)
		gh_rm_minidump_deregister_slot((u16)slot);
	kfree(mi_direct_regions);
	if (mi_kmsg_buf)
		free_pages_exact(mi_kmsg_buf, MI_MD_KMSG_SIZE);
}

module_init(mi_md_kmsg_init);
module_exit(mi_md_kmsg_exit);

MODULE_SOFTDEP("pre: smem qcom-scm spmi-pmic-arb nvmem_qcom-spmi-sdam gh_rm_drv minidump");
MODULE_DESCRIPTION("Xiaomi SM8650 BlackBox md_kmsg Minidump Driver");
MODULE_LICENSE("GPL");
