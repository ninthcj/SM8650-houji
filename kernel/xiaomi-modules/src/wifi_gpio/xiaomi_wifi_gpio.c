// SPDX-License-Identifier: GPL-2.0-only
/*
 * Xiaomi Wi-Fi SAR antenna GPIO netlink driver (Linux 6.12 GKI 2.0 port)
 *
 * Matches Xiaomi 14 (houji) DT node:
 *   compatible = "xiaomi,xiaomi-wifi";
 *   gpio = <&tlmm 0x2e 0x00>;
 *   debounce-time = <50>;
 *
 * Broadcasts GPIO status over Generic Netlink family "wifi_sar",
 * multicast group "sar_event", command SAR_CMD_GPIO_STATUS (2),
 * attribute SAR_ATTR_STATUS (1, NLA_U32).
 */

#include <linux/device.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeirq.h>
#include <linux/pm_wakeup.h>
#include <linux/workqueue.h>
#include <net/genetlink.h>
#include <net/netlink.h>

#include "../hwid/hwid.h"

#define WIFI_SAR_FAMILY_NAME	"wifi_sar"
#define WIFI_SAR_MCGRP_NAME	"sar_event"
#define WIFI_SAR_VERSION	1
#define DEFAULT_DEBOUNCE_MS	50

enum sar_attrs {
	SAR_ATTR_UNSPEC = 0,
	SAR_ATTR_STATUS = 1,
	__SAR_ATTR_MAX,
};
#define SAR_ATTR_MAX (__SAR_ATTR_MAX - 1)

enum sar_cmds {
	SAR_CMD_UNSPEC = 0,
	SAR_CMD_GET_STATUS = 1,
	SAR_CMD_GPIO_STATUS = 2,
	__SAR_CMD_MAX,
};

struct ant_gpio_data {
	struct device *dev;
	u32 debounce_time;
	int irq;
	struct gpio_desc *gpiod;
	struct delayed_work debounce_work;
};

static struct gpio_desc *g_wifi_gpiod;
static int g_sar_status;

static const struct nla_policy sar_policy[SAR_ATTR_MAX + 1] = {
	[SAR_ATTR_STATUS] = { .type = NLA_U32 },
};

static const struct genl_multicast_group sar_mcgrps[] = {
	{ .name = WIFI_SAR_MCGRP_NAME },
};

static int sar_send_status(int status);

static int sar_status_get_doit(struct sk_buff *skb, struct genl_info *info)
{
	if (!g_wifi_gpiod)
		return -ENODEV;

	g_sar_status = gpiod_get_raw_value(g_wifi_gpiod);
	return sar_send_status(g_sar_status);
}

static const struct genl_ops sar_ops[] = {
	{
		.cmd = SAR_CMD_GET_STATUS,
		.doit = sar_status_get_doit,
	},
};

static struct genl_family sar_fam __ro_after_init = {
	.name = WIFI_SAR_FAMILY_NAME,
	.version = WIFI_SAR_VERSION,
	.maxattr = SAR_ATTR_MAX,
	.policy = sar_policy,
	.module = THIS_MODULE,
	.ops = sar_ops,
	.n_ops = ARRAY_SIZE(sar_ops),
	.mcgrps = sar_mcgrps,
	.n_mcgrps = ARRAY_SIZE(sar_mcgrps),
};

static int sar_send_status(int status)
{
	struct sk_buff *skb;
	void *msg_head;
	int ret;

	skb = genlmsg_new(NLMSG_GOODSIZE, GFP_KERNEL);
	if (!skb)
		return -ENOMEM;

	msg_head = genlmsg_put(skb, 0, 0, &sar_fam, 0, SAR_CMD_GPIO_STATUS);
	if (!msg_head) {
		nlmsg_free(skb);
		return -EMSGSIZE;
	}

	ret = nla_put_u32(skb, SAR_ATTR_STATUS, status);
	if (ret) {
		genlmsg_cancel(skb, msg_head);
		nlmsg_free(skb);
		return ret;
	}

	genlmsg_end(skb, msg_head);

	ret = genlmsg_multicast(&sar_fam, skb, 0, 0, GFP_KERNEL);
	if (ret && ret != -ESRCH) {
		pr_err("sar send status failed: %d\n", ret);
		return ret;
	}

	return 0;
}

static void gpio_debounce_work(struct work_struct *work)
{
	struct ant_gpio_data *data =
		container_of(work, struct ant_gpio_data, debounce_work.work);

	g_sar_status = gpiod_get_raw_value(data->gpiod);
	dev_info(data->dev, "gpio status: %d\n", g_sar_status);
	sar_send_status(g_sar_status);
	pm_relax(data->dev);
}

static irqreturn_t gpio_testing_threaded_irq_handler(int irq, void *dev_id)
{
	struct ant_gpio_data *data = dev_id;

	dev_dbg(data->dev, "irq [%d] triggered\n", irq);
	pm_stay_awake(data->dev);
	mod_delayed_work(system_wq, &data->debounce_work,
			 msecs_to_jiffies(data->debounce_time));
	return IRQ_HANDLED;
}

static int xiaomi_wifi_gpio_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct ant_gpio_data *data;
	int ret;

	data = devm_kzalloc(dev, sizeof(*data), GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	data->dev = dev;
	if (of_property_read_u32(dev->of_node, "debounce-time", &data->debounce_time)) {
		dev_info(dev, "Failed to get debounce-time, use default.\n");
		data->debounce_time = DEFAULT_DEBOUNCE_MS;
	}

	data->gpiod = devm_gpiod_get(dev, NULL, GPIOD_IN);
	if (IS_ERR(data->gpiod)) {
		ret = PTR_ERR(data->gpiod);
		dev_err(dev, "Failed to get ant gpio %d\n", ret);
		return ret;
	}

	g_wifi_gpiod = data->gpiod;
	data->irq = gpiod_to_irq(data->gpiod);
	if (data->irq < 0) {
		dev_err(dev, "Failed to map ant gpio to irq %d\n", data->irq);
		return data->irq;
	}

	INIT_DELAYED_WORK(&data->debounce_work, gpio_debounce_work);

	ret = devm_request_threaded_irq(dev, data->irq, NULL,
					gpio_testing_threaded_irq_handler,
					IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
					"xiaomi_wifi_gpio", data);
	if (ret < 0) {
		dev_err(dev, "Failed to request irq.\n");
		return -EBUSY;
	}

	device_set_wakeup_capable(dev, true);
	ret = device_wakeup_enable(dev);
	if (ret) {
		dev_err(dev, "Failed to configure device as wakeup %d\n", ret);
		return ret;
	}

	ret = dev_pm_set_wake_irq(dev, data->irq);
	if (ret) {
		dev_err(dev, "Failed to set wake irq %d\n", ret);
		device_wakeup_disable(dev);
		return ret;
	}

	platform_set_drvdata(pdev, data);
	pr_info("xiaomi_wifi_gpio driver probed successfully.\n");
	return 0;
}

static void xiaomi_wifi_gpio_remove(struct platform_device *pdev)
{
	struct ant_gpio_data *data = platform_get_drvdata(pdev);

	if (data) {
		cancel_delayed_work_sync(&data->debounce_work);
		dev_pm_clear_wake_irq(&pdev->dev);
		device_wakeup_disable(&pdev->dev);
	}
	g_wifi_gpiod = NULL;
}

static const struct of_device_id gpio_testing_mode_of_match[] = {
	{ .compatible = "xiaomi,xiaomi-wifi" },
	{ },
};
MODULE_DEVICE_TABLE(of, gpio_testing_mode_of_match);

static struct platform_driver xiaomi_wifi_gpio_driver = {
	.probe = xiaomi_wifi_gpio_probe,
	.remove = xiaomi_wifi_gpio_remove,
	.driver = {
		.name = "xiaomi_wifi_gpio",
		.of_match_table = gpio_testing_mode_of_match,
	},
};

static int __init wifi_gpio_driver_init(void)
{
	int ret;

	if (get_hw_country_version() != CountryCN) {
		pr_info("%s not cn devices, not init\n", __func__);
		return 0;
	}

	ret = genl_register_family(&sar_fam);
	if (ret)
		return ret;

	ret = platform_driver_register(&xiaomi_wifi_gpio_driver);
	if (ret) {
		genl_unregister_family(&sar_fam);
		return ret;
	}

	return 0;
}

static void __exit wifi_gpio_driver_exit(void)
{
	if (get_hw_country_version() != CountryCN) {
		pr_info("%s not cn devices, exit gpio-testing\n", __func__);
		return;
	}

	pr_info("%s cn version, exit gpio-testing\n", __func__);
	platform_driver_unregister(&xiaomi_wifi_gpio_driver);
	genl_unregister_family(&sar_fam);
}

module_init(wifi_gpio_driver_init);
module_exit(wifi_gpio_driver_exit);

MODULE_AUTHOR("songgang3@xiaomi.com");
MODULE_DESCRIPTION("Wifi Gpio Testing Driver for Xiaomi Corporation");
MODULE_LICENSE("GPL v2");
