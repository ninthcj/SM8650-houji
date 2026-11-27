// SPDX-License-Identifier: GPL-2.0-only
/*
 * Dump display support (Linux 6.12 GKI 2.0 port)
 *
 * Copyright (C) 2023 Xiaomi, Inc.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/io.h>
#include <linux/init.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/panic_notifier.h>
#include <linux/platform_device.h>
#include <linux/printk.h>
#include <linux/sched.h>
#include <linux/sizes.h>
#include <linux/slab.h>
#include <linux/stacktrace.h>
#include <linux/string.h>

#define CRASH_RECORD_MAGIC	0x12345678
#define TRACE_INFO_SIZE		2044

struct crash_info {
	unsigned int magic;
	char back_trace[TRACE_INFO_SIZE];
};

static struct crash_info __iomem *crash_info_region_base;

static int trace_snprint(char *buf, size_t size, const unsigned long *entries,
			 unsigned int nr_entries)
{
	unsigned int generated, i, total = 0;

	if (!entries)
		return 0;

	for (i = 0; i < nr_entries && size; i++) {
		generated = snprintf(buf, size, "\n\r%pS", (void *)entries[i]);
		total += generated;
		if (generated >= size) {
			buf += size;
			size = 0;
		} else {
			buf += generated;
			size -= generated;
		}
	}
	if (size)
		snprintf(buf, size, "\n\rcomm:%s", current->comm);
	return total;
}

static int set_backtrace_msg(struct notifier_block *self, unsigned long v, void *p)
{
	unsigned int nr_entries;
	unsigned long entries[30] = { 0 };
	char *trace_record;

	if (!crash_info_region_base)
		return NOTIFY_DONE;

	nr_entries = stack_trace_save(entries, ARRAY_SIZE(entries), 2);
	if (!nr_entries)
		return NOTIFY_DONE;

	trace_record = kzalloc(TRACE_INFO_SIZE, GFP_ATOMIC);
	if (!trace_record)
		return NOTIFY_DONE;

	trace_snprint(trace_record, TRACE_INFO_SIZE, entries, nr_entries);
	memcpy_toio(crash_info_region_base->back_trace, trace_record, TRACE_INFO_SIZE);

	kfree(trace_record);
	return NOTIFY_DONE;
}

static struct notifier_block panic_block_to_dump = {
	.notifier_call = set_backtrace_msg,
};

static int dump_display_probe(struct platform_device *pdev)
{
	static const char unknown_info[] = "UNKNOWN";
	struct resource *res;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res) {
		pr_err("failed to get dump_display address\n");
		return -EINVAL;
	}

	crash_info_region_base = devm_ioremap(&pdev->dev, res->start,
					      resource_size(res));
	if (!crash_info_region_base) {
		pr_err("error to ioremap crash_record base\n");
		return -EIO;
	}

	memset_io(crash_info_region_base, 0, resource_size(res));
	writel(CRASH_RECORD_MAGIC, &crash_info_region_base->magic);
	memcpy_toio(crash_info_region_base->back_trace, unknown_info,
		    sizeof(unknown_info));

	atomic_notifier_chain_register(&panic_notifier_list, &panic_block_to_dump);
	return 0;
}

static void dump_display_remove(struct platform_device *pdev)
{
	atomic_notifier_chain_unregister(&panic_notifier_list, &panic_block_to_dump);
	crash_info_region_base = NULL;
}

static const struct of_device_id ddp_match[] = {
	{ .compatible = "xiaomi,dump_display" },
	{ },
};
MODULE_DEVICE_TABLE(of, ddp_match);

static struct platform_driver dump_display_driver = {
	.probe		= dump_display_probe,
	.remove		= dump_display_remove,
	.driver		= {
		.name		= "dump_display",
		.of_match_table	= ddp_match,
	},
};

module_platform_driver(dump_display_driver);

MODULE_DESCRIPTION("Xiaomi crash dump display region logger");
MODULE_LICENSE("GPL v2");
