// SPDX-License-Identifier: GPL-2.0-only
/*
 * Xiaomi thermal control interface (Linux 6.12 strict GKI 2.0 port)
 *
 * Exposes /sys/class/thermal/thermal_message/{sconfig,cpu_limits,screen_state,...}
 * using ONLY exported GKI 2.0 KMI symbols (kernel_kobj, kernfs_find_and_get_ns,
 * kobject_create_and_add, sysfs_create_group, sysfs_notify) with zero dependency
 * on private kernel internal headers (../base/base.h or thermal_core.h).
 *
 * Preserves 100% of stock HyperOS 3 OS3.0.307.0 mi_thermal_interface.ko sysfs ABI.
 */

#include <linux/atomic.h>
#include <linux/cpufreq.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/kernfs.h>
#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pm_qos.h>
#include <linux/power_supply.h>
#include <linux/rcupdate.h>
#include <linux/slab.h>
#include <linux/soc/qcom/panel_event_notifier.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/workqueue.h>

#if defined(CONFIG_DRM_PANEL)
#include <drm/drm_panel.h>
static struct drm_panel *prim_panel;
#endif

#define BOOST_BUFFER_SIZE	128
#define BOARD_BUFFER_SIZE	128

struct mi_thermal_device {
	struct kobject *kobj;
	struct attribute_group attrs;
};

struct freq_table {
	u32 frequency;
};

struct cpufreq_device {
	int id;
	unsigned int cpufreq_state;
	unsigned int max_level;
	struct freq_table *freq_table; /* In descending order */
	struct cpufreq_policy *policy;
	struct list_head node;
	struct freq_qos_request *qos_req;
};

#ifdef CONFIG_MI_THERMAL_MULTI_CHARGE
struct usb_monitor {
	struct notifier_block psy_nb;
	struct work_struct usb_state_work;
	int usb_online;
};
static struct usb_monitor usb_state;
static atomic_t charger_mode = ATOMIC_INIT(-1);
#endif

static struct mi_thermal_device mi_thermal_dev;
static int screen_state;
static int screen_light;
static int screen_last_status;
static void *cookie;

static atomic_t temp_state = ATOMIC_INIT(0);
static atomic_t switch_mode = ATOMIC_INIT(-1);
static atomic_t balance_mode = ATOMIC_INIT(0);
static atomic_t modem_limit = ATOMIC_INIT(0);
static atomic_t market_download_limit = ATOMIC_INIT(0);
static atomic_t board_sensor_temp_comp_default = ATOMIC_INIT(0);
static atomic_t cpu_nolimit_temp_default = ATOMIC_INIT(0);
static atomic_t poor_modem_limit = ATOMIC_INIT(0);
static atomic_t wifi_limit = ATOMIC_INIT(0);
static atomic_t flash_state = ATOMIC_INIT(0);
static atomic_t modem_rate = ATOMIC_INIT(0);
static atomic_t modem_level = ATOMIC_INIT(0);
/* HyperOS 3 OS3.0.307.0 additional thermal interface controls */
static atomic_t boot_complete = ATOMIC_INIT(0);
static atomic_t ntn_limit = ATOMIC_INIT(0);
static atomic_t power_level = ATOMIC_INIT(0);
static atomic_t powersave_mode = ATOMIC_INIT(0);
static atomic_t thermal_max_brightness = ATOMIC_INIT(0);
static atomic_t torch_level = ATOMIC_INIT(0);
static atomic_t torch_real_level = ATOMIC_INIT(0);
static atomic_t voice_limit = ATOMIC_INIT(0);

static char boost_buf[BOOST_BUFFER_SIZE];
static const char *board_sensor;
static char board_sensor_temp[BOARD_BUFFER_SIZE];
static char board_sensor_second_temp[BOARD_BUFFER_SIZE];
static char board_sensor_other_temp[BOARD_BUFFER_SIZE];
static char ambient_sensor_temp[BOARD_BUFFER_SIZE];
static char display_therm_temp[BOARD_BUFFER_SIZE];

static LIST_HEAD(cpufreq_dev_list);
static DEFINE_MUTEX(cpufreq_list_lock);
static DEFINE_PER_CPU(struct freq_qos_request, qos_req);

static struct workqueue_struct *screen_state_wq;
static struct delayed_work screen_state_dw;

static int cpufreq_set_level(struct cpufreq_device *cdev, unsigned long state)
{
	if (WARN_ON(state > cdev->max_level))
		return -EINVAL;

	if (cdev->cpufreq_state == state)
		return 0;

	cdev->cpufreq_state = state;
	return freq_qos_update_request(cdev->qos_req,
				       cdev->freq_table[state].frequency);
}

static void cpu_limits_set_level(unsigned int cpu, unsigned int max_freq)
{
	struct cpufreq_device *cpufreq_dev;
	unsigned int level = 0;

	mutex_lock(&cpufreq_list_lock);
	list_for_each_entry(cpufreq_dev, &cpufreq_dev_list, node) {
		if (cpufreq_dev->id == cpu) {
			for (level = 0; level <= cpufreq_dev->max_level; level++) {
				unsigned int target_freq =
					cpufreq_dev->freq_table[level].frequency;
				if (max_freq >= target_freq) {
					cpufreq_set_level(cpufreq_dev, level);
					break;
				}
			}
			break;
		}
	}
	mutex_unlock(&cpufreq_list_lock);
}

static unsigned int find_next_max(struct cpufreq_frequency_table *table,
				  unsigned int prev_max)
{
	struct cpufreq_frequency_table *pos;
	unsigned int max = 0;

	cpufreq_for_each_valid_entry(pos, table) {
		if (pos->frequency > max && pos->frequency < prev_max)
			max = pos->frequency;
	}
	return max;
}

static int cpu_thermal_init(void)
{
	int cpu, ret = 0;
	struct cpufreq_policy *policy;
	struct freq_qos_request *req;

	for_each_possible_cpu(cpu) {
		unsigned int i, freq;
		struct cpufreq_device *cpufreq_dev;

		req = &per_cpu(qos_req, cpu);
		policy = cpufreq_cpu_get(cpu);
		if (!policy) {
			pr_warn("%s: cpufreq policy not found for cpu%d\n",
				__func__, cpu);
			continue;
		}

		i = cpufreq_table_count_valid_entries(policy);
		if (!i) {
			cpufreq_cpu_put(policy);
			continue;
		}

		cpufreq_dev = kzalloc(sizeof(*cpufreq_dev), GFP_KERNEL);
		if (!cpufreq_dev) {
			cpufreq_cpu_put(policy);
			return -ENOMEM;
		}

		cpufreq_dev->policy = policy;
		cpufreq_dev->qos_req = req;
		cpufreq_dev->max_level = i - 1;
		cpufreq_dev->id = policy->cpu;

		cpufreq_dev->freq_table = kmalloc_array(
			i, sizeof(*cpufreq_dev->freq_table), GFP_KERNEL);
		if (!cpufreq_dev->freq_table) {
			kfree(cpufreq_dev);
			cpufreq_cpu_put(policy);
			return -ENOMEM;
		}

		for (i = 0, freq = UINT_MAX; i <= cpufreq_dev->max_level; i++) {
			freq = find_next_max(policy->freq_table, freq);
			cpufreq_dev->freq_table[i].frequency = freq;
		}

		ret = freq_qos_add_request(&policy->constraints,
					   cpufreq_dev->qos_req, FREQ_QOS_MAX,
					   cpufreq_dev->freq_table[0].frequency);
		if (ret < 0) {
			pr_err("%s: Failed to add freq constraint (%d)\n",
			       __func__, ret);
			kfree(cpufreq_dev->freq_table);
			kfree(cpufreq_dev);
			cpufreq_cpu_put(policy);
			return ret;
		}

		mutex_lock(&cpufreq_list_lock);
		list_add(&cpufreq_dev->node, &cpufreq_dev_list);
		mutex_unlock(&cpufreq_list_lock);
	}
	return ret;
}

static void destroy_thermal_cpu(void)
{
	struct cpufreq_device *priv, *tmp;

	mutex_lock(&cpufreq_list_lock);
	list_for_each_entry_safe(priv, tmp, &cpufreq_dev_list, node) {
		freq_qos_remove_request(priv->qos_req);
		list_del(&priv->node);
		cpufreq_cpu_put(priv->policy);
		kfree(priv->freq_table);
		kfree(priv);
	}
	mutex_unlock(&cpufreq_list_lock);
}

#define MI_THERMAL_ATOMIC_ATTR(_name, _var)				\
static ssize_t _name##_show(struct kobject *kobj,			\
			    struct kobj_attribute *attr, char *buf)	\
{									\
	return sysfs_emit(buf, "%d\n", atomic_read(&(_var)));		\
}									\
static ssize_t _name##_store(struct kobject *kobj,			\
			     struct kobj_attribute *attr,		\
			     const char *buf, size_t len)		\
{									\
	int val = -1;							\
	if (kstrtoint(buf, 10, &val))					\
		return -EINVAL;						\
	atomic_set(&(_var), val);					\
	return len;							\
}									\
static struct kobj_attribute mi_attr_##_name =				\
	__ATTR(_name, 0664, _name##_show, _name##_store)

#define MI_THERMAL_STR_ATTR(_name, _buf, _size, _mode)			\
static ssize_t _name##_show(struct kobject *kobj,			\
			    struct kobj_attribute *attr, char *buf)	\
{									\
	return sysfs_emit(buf, "%s", _buf);				\
}									\
static ssize_t _name##_store(struct kobject *kobj,			\
			     struct kobj_attribute *attr,		\
			     const char *buf, size_t len)		\
{									\
	strscpy(_buf, buf, _size);					\
	return len;							\
}									\
static struct kobj_attribute mi_attr_##_name =				\
	__ATTR(_name, _mode, _name##_show, _name##_store)

MI_THERMAL_ATOMIC_ATTR(temp_state, temp_state);
MI_THERMAL_ATOMIC_ATTR(sconfig, switch_mode);
MI_THERMAL_ATOMIC_ATTR(balance_mode, balance_mode);
MI_THERMAL_ATOMIC_ATTR(market_download_limit, market_download_limit);
MI_THERMAL_ATOMIC_ATTR(modem_limit, modem_limit);
MI_THERMAL_ATOMIC_ATTR(wifi_limit, wifi_limit);
MI_THERMAL_ATOMIC_ATTR(flash_state, flash_state);
MI_THERMAL_ATOMIC_ATTR(board_sensor_temp_comp, board_sensor_temp_comp_default);
MI_THERMAL_ATOMIC_ATTR(poor_modem_limit, poor_modem_limit);
MI_THERMAL_ATOMIC_ATTR(cpu_nolimit_temp, cpu_nolimit_temp_default);
MI_THERMAL_ATOMIC_ATTR(modem_rate, modem_rate);
MI_THERMAL_ATOMIC_ATTR(modem_level, modem_level);
MI_THERMAL_ATOMIC_ATTR(boot_complete, boot_complete);
MI_THERMAL_ATOMIC_ATTR(ntn_limit, ntn_limit);
MI_THERMAL_ATOMIC_ATTR(power_level, power_level);
MI_THERMAL_ATOMIC_ATTR(powersave_mode, powersave_mode);
MI_THERMAL_ATOMIC_ATTR(thermal_max_brightness, thermal_max_brightness);
MI_THERMAL_ATOMIC_ATTR(torch_level, torch_level);
MI_THERMAL_ATOMIC_ATTR(torch_real_level, torch_real_level);
MI_THERMAL_ATOMIC_ATTR(voice_limit, voice_limit);

MI_THERMAL_STR_ATTR(boost, boost_buf, BOOST_BUFFER_SIZE, 0644);
MI_THERMAL_STR_ATTR(board_sensor_temp, board_sensor_temp, BOARD_BUFFER_SIZE, 0664);
MI_THERMAL_STR_ATTR(board_sensor_second_temp, board_sensor_second_temp, BOARD_BUFFER_SIZE, 0664);
MI_THERMAL_STR_ATTR(board_sensor_other_temp, board_sensor_other_temp, BOARD_BUFFER_SIZE, 0664);
MI_THERMAL_STR_ATTR(ambient_sensor_temp, ambient_sensor_temp, BOARD_BUFFER_SIZE, 0664);
MI_THERMAL_STR_ATTR(display_therm_temp, display_therm_temp, BOARD_BUFFER_SIZE, 0664);

static ssize_t cpu_limits_show(struct kobject *kobj,
			       struct kobj_attribute *attr, char *buf)
{
	return 0;
}

static ssize_t cpu_limits_store(struct kobject *kobj,
				struct kobj_attribute *attr, const char *buf,
				size_t len)
{
	unsigned int cpu, max;

	if (sscanf(buf, "cpu%u %u", &cpu, &max) != 2) {
		pr_err("input param error, can not parse param\n");
		return -EINVAL;
	}
	cpu_limits_set_level(cpu, max);
	return len;
}
static struct kobj_attribute mi_attr_cpu_limits =
	__ATTR(cpu_limits, 0664, cpu_limits_show, cpu_limits_store);

static ssize_t screen_state_show(struct kobject *kobj,
				 struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", screen_state);
}
static struct kobj_attribute mi_attr_screen_state =
	__ATTR(screen_state, 0664, screen_state_show, NULL);

static ssize_t board_sensor_show(struct kobject *kobj,
				 struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%s", board_sensor ? board_sensor : "invalid");
}
static struct kobj_attribute mi_attr_board_sensor =
	__ATTR(board_sensor, 0664, board_sensor_show, NULL);

static ssize_t ambient_sensor_show(struct kobject *kobj,
				   struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%s", "ABT-SENSOR");
}
static struct kobj_attribute mi_attr_ambient_sensor =
	__ATTR(ambient_sensor, 0664, ambient_sensor_show, NULL);

#ifdef CONFIG_MI_THERMAL_MULTI_CHARGE
MI_THERMAL_ATOMIC_ATTR(charger_temp, charger_mode);

static ssize_t usb_online_show(struct kobject *kobj,
			       struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", usb_state.usb_online);
}
static struct kobj_attribute mi_attr_usb_online =
	__ATTR(usb_online, 0664, usb_online_show, NULL);

static int usb_online_callback(struct notifier_block *nb, unsigned long val,
			       void *data)
{
	struct power_supply *psy = data;

	if (strcmp(psy->desc->name, "usb"))
		return NOTIFY_OK;
	schedule_work(&usb_state.usb_state_work);
	return NOTIFY_OK;
}

static void usb_online_work(struct work_struct *work)
{
	static struct power_supply *usb_psy;
	union power_supply_propval ret = { 0 };
	int err;

	if (!usb_psy)
		usb_psy = power_supply_get_by_name("usb");

	if (usb_psy) {
		err = power_supply_get_property(usb_psy,
						POWER_SUPPLY_PROP_ONLINE, &ret);
		if (err) {
			pr_err("usb online read error:%d\n", err);
			return;
		}
		usb_state.usb_online = ret.intval;
		if (mi_thermal_dev.kobj)
			sysfs_notify(mi_thermal_dev.kobj, NULL, "usb_online");
	}
}
#endif

static int of_parse_thermal_message(void)
{
	struct device_node *np;
	int ret;

	np = of_find_node_by_name(NULL, "thermal-message");
	if (!np)
		return -EINVAL;

	ret = of_property_read_string(np, "board-sensor", &board_sensor);
	of_node_put(np);
	if (ret)
		return -EINVAL;

	pr_info("%s board sensor: %s\n", __func__, board_sensor);
	return 0;
}

static struct attribute *mi_thermal_dev_attr_group[] = {
	&mi_attr_temp_state.attr,
	&mi_attr_cpu_limits.attr,
	&mi_attr_sconfig.attr,
	&mi_attr_screen_state.attr,
	&mi_attr_boost.attr,
	&mi_attr_board_sensor.attr,
	&mi_attr_board_sensor_temp.attr,
	&mi_attr_board_sensor_second_temp.attr,
	&mi_attr_board_sensor_other_temp.attr,
	&mi_attr_balance_mode.attr,
	&mi_attr_modem_limit.attr,
	&mi_attr_wifi_limit.attr,
	&mi_attr_flash_state.attr,
	&mi_attr_market_download_limit.attr,
	&mi_attr_board_sensor_temp_comp.attr,
	&mi_attr_poor_modem_limit.attr,
	&mi_attr_cpu_nolimit_temp.attr,
	&mi_attr_modem_rate.attr,
	&mi_attr_modem_level.attr,
	&mi_attr_ambient_sensor.attr,
	&mi_attr_ambient_sensor_temp.attr,
	&mi_attr_boot_complete.attr,
	&mi_attr_display_therm_temp.attr,
	&mi_attr_ntn_limit.attr,
	&mi_attr_power_level.attr,
	&mi_attr_powersave_mode.attr,
	&mi_attr_thermal_max_brightness.attr,
	&mi_attr_torch_level.attr,
	&mi_attr_torch_real_level.attr,
	&mi_attr_voice_limit.attr,
#ifdef CONFIG_MI_THERMAL_MULTI_CHARGE
	&mi_attr_charger_temp.attr,
	&mi_attr_usb_online.attr,
#endif
	NULL,
};

#if defined(CONFIG_DRM_PANEL)
static const char *get_screen_state_name(int mode)
{
	switch (mode) {
	case DRM_PANEL_EVENT_UNBLANK:
		return "On";
	case DRM_PANEL_EVENT_BLANK_LP:
		return "Doze";
	case DRM_PANEL_EVENT_BLANK:
		return "Off";
	default:
		return "Unknown";
	}
}

static void screen_state_for_thermal_callback(
	enum panel_event_notifier_tag tag,
	struct panel_event_notification *notification, void *client_data)
{
	if (!notification || notification->notif_data.early_trigger)
		return;

	if (tag == PANEL_EVENT_NOTIFICATION_PRIMARY) {
		switch (notification->notif_type) {
		case DRM_PANEL_EVENT_UNBLANK:
			screen_light |= 0x1;
			break;
		case DRM_PANEL_EVENT_BLANK:
		case DRM_PANEL_EVENT_BLANK_LP:
			screen_light &= 0x2;
			break;
		case DRM_PANEL_EVENT_FPS_CHANGE:
			return;
		default:
			return;
		}
		pr_debug("%s: %s, screen_light = %d\n", __func__,
			 get_screen_state_name(notification->notif_type),
			 screen_light);
	}

	screen_state = screen_light ? 1 : 0;
	if (screen_last_status != screen_state && mi_thermal_dev.kobj) {
		sysfs_notify(mi_thermal_dev.kobj, NULL, "screen_state");
		screen_last_status = screen_state;
	}
}

static int thermal_check_panel(struct device_node *np)
{
	int i, count;
	struct device_node *node;
	struct drm_panel *panel;

	count = of_count_phandle_with_args(np, "panel", NULL);
	if (count <= 0)
		return 0;

	for (i = 0; i < count; i++) {
		node = of_parse_phandle(np, "panel", i);
		panel = of_drm_find_panel(node);
		of_node_put(node);
		if (!IS_ERR(panel)) {
			prim_panel = panel;
			break;
		}
		prim_panel = NULL;
	}
	return 0;
}

static void screen_state_check(struct work_struct *work)
{
	struct device_node *node;
	static int retry_count = 10;

	node = of_find_node_by_name(NULL, "thermal-screen");
	if (!node)
		return;

	thermal_check_panel(node);
	of_node_put(node);

	if (prim_panel) {
		if (!cookie) {
			cookie = panel_event_notifier_register(
				PANEL_EVENT_NOTIFICATION_PRIMARY,
				PANEL_EVENT_NOTIFIER_CLIENT_THERMAL, prim_panel,
				screen_state_for_thermal_callback, NULL);
			if (IS_ERR(cookie)) {
				pr_err("%s: Failed to register prim_panel events\n",
				       __func__);
				cookie = NULL;
			}
		}
	} else if (retry_count > 0) {
		retry_count--;
		queue_delayed_work(screen_state_wq, &screen_state_dw, 5 * HZ);
	}
}
#endif

static struct mi_thermal_device mi_powersave_dev;

static struct attribute *mi_powersave_dev_attr_group[] = {
	&mi_attr_powersave_mode.attr,
	&mi_attr_power_level.attr,
	NULL,
};

static void create_thermal_message_node(void)
{
	struct kernfs_node *sysfs_sd = NULL;
	struct kernfs_node *class_sd = NULL;
	struct kernfs_node *thermal_sd = NULL;
	struct kobject *thermal_kobj = NULL;
	int ret;

	/*
	 * kernfs_get_parent()/kernfs_get() are not in the GKI 6.18 KMI export
	 * list; kernel_kobj (/sys/kernel) is never reparented and its parent
	 * is the permanent sysfs root node, so RCU-dereferencing __parent
	 * before sleeping in kernfs_find_and_get() is safe without a ref.
	 */
	rcu_read_lock();
	sysfs_sd = (kernel_kobj && kernel_kobj->sd) ?
		rcu_dereference(kernel_kobj->sd->__parent) : NULL;
	rcu_read_unlock();
	if (!sysfs_sd) {
		pr_err("%s: sysfs_sd is NULL\n", __func__);
		return;
	}

	class_sd = kernfs_find_and_get(sysfs_sd, "class");
	if (!class_sd) {
		pr_err("%s: cannot find class_sd\n", __func__);
		return;
	}

	thermal_sd = kernfs_find_and_get(class_sd, "thermal");
	kernfs_put(class_sd);
	if (!thermal_sd) {
		pr_err("%s: cannot find thermal_sd\n", __func__);
		return;
	}

	thermal_kobj = (struct kobject *)thermal_sd->priv;
	if (!thermal_kobj) {
		pr_err("%s: cannot find thermal kobj\n", __func__);
		kernfs_put(thermal_sd);
		return;
	}

	mi_thermal_dev.kobj = kobject_create_and_add("thermal_message", thermal_kobj);
	if (!mi_thermal_dev.kobj) {
		pr_err("%s: failed to create thermal_message kobj\n", __func__);
	} else {
		mi_thermal_dev.attrs.attrs = mi_thermal_dev_attr_group;
		ret = sysfs_create_group(mi_thermal_dev.kobj, &mi_thermal_dev.attrs);
		if (ret) {
			pr_err("%s: cannot create sysfs group: %d\n", __func__, ret);
			kobject_put(mi_thermal_dev.kobj);
			mi_thermal_dev.kobj = NULL;
		}
	}

	mi_powersave_dev.kobj = kobject_create_and_add("power_save", thermal_kobj);
	if (!mi_powersave_dev.kobj) {
		pr_err("%s: failed to create power_save kobj\n", __func__);
	} else {
		mi_powersave_dev.attrs.attrs = mi_powersave_dev_attr_group;
		ret = sysfs_create_group(mi_powersave_dev.kobj, &mi_powersave_dev.attrs);
		if (ret) {
			pr_err("%s: cannot create power_save sysfs group: %d\n", __func__, ret);
			kobject_put(mi_powersave_dev.kobj);
			mi_powersave_dev.kobj = NULL;
		}
	}

	kernfs_put(thermal_sd);
}

static void destroy_thermal_message_node(void)
{
	if (mi_powersave_dev.kobj) {
		sysfs_remove_group(mi_powersave_dev.kobj, &mi_powersave_dev.attrs);
		kobject_put(mi_powersave_dev.kobj);
		mi_powersave_dev.kobj = NULL;
	}
	if (mi_thermal_dev.kobj) {
		sysfs_remove_group(mi_thermal_dev.kobj, &mi_thermal_dev.attrs);
		kobject_put(mi_thermal_dev.kobj);
		mi_thermal_dev.kobj = NULL;
	}
}

static int __init mi_thermal_interface_init(void)
{
	int result;

#if defined(CONFIG_OF) && defined(CONFIG_DRM_PANEL)
	screen_state_wq = create_singlethread_workqueue("screen_state_wq");
	if (screen_state_wq) {
		INIT_DELAYED_WORK(&screen_state_dw, screen_state_check);
		queue_delayed_work(screen_state_wq, &screen_state_dw, 5 * HZ);
	}
#endif

	cpu_thermal_init();
	result = of_parse_thermal_message();
	if (result)
		pr_warn("%s: cannot parse thermal-message DT node (%d)\n",
			__func__, result);

	create_thermal_message_node();

#ifdef CONFIG_MI_THERMAL_MULTI_CHARGE
	INIT_WORK(&usb_state.usb_state_work, usb_online_work);
	usb_state.psy_nb.notifier_call = usb_online_callback;
	result = power_supply_reg_notifier(&usb_state.psy_nb);
	if (result < 0)
		pr_err("usb online notifier registration error: %d\n", result);
#endif

	return 0;
}

static void __exit mi_thermal_interface_exit(void)
{
#ifdef CONFIG_MI_THERMAL_MULTI_CHARGE
	power_supply_unreg_notifier(&usb_state.psy_nb);
	cancel_work_sync(&usb_state.usb_state_work);
#endif

#if defined(CONFIG_OF) && defined(CONFIG_DRM_PANEL)
	if (screen_state_wq) {
		cancel_delayed_work_sync(&screen_state_dw);
		destroy_workqueue(screen_state_wq);
	}

	if (prim_panel && cookie && !IS_ERR(cookie))
		panel_event_notifier_unregister(cookie);
#endif

	destroy_thermal_message_node();
	destroy_thermal_cpu();
}

module_init(mi_thermal_interface_init);
module_exit(mi_thermal_interface_exit);

MODULE_AUTHOR("Xiaomi thermal team");
MODULE_DESCRIPTION("Xiaomi thermal control interface");
MODULE_LICENSE("GPL v2");
