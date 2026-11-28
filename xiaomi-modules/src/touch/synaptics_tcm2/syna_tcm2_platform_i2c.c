// SPDX-License-Identifier: GPL-2.0
/*
 * Synaptics TouchCom touchscreen driver
 *
 * Copyright (C) 2017-2020 Synaptics Incorporated. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * INFORMATION CONTAINED IN THIS DOCUMENT IS PROVIDED "AS-IS," AND SYNAPTICS
 * EXPRESSLY DISCLAIMS ALL EXPRESS AND IMPLIED WARRANTIES, INCLUDING ANY
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE,
 * AND ANY WARRANTIES OF NON-INFRINGEMENT OF ANY INTELLECTUAL PROPERTY RIGHTS.
 * IN NO EVENT SHALL SYNAPTICS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, PUNITIVE, OR CONSEQUENTIAL DAMAGES ARISING OUT OF OR IN CONNECTION
 * WITH THE USE OF THE INFORMATION CONTAINED IN THIS DOCUMENT, HOWEVER CAUSED
 * AND BASED ON ANY THEORY OF LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
 * NEGLIGENCE OR OTHER TORTIOUS ACTION, AND EVEN IF SYNAPTICS WAS ADVISED OF
 * THE POSSIBILITY OF SUCH DAMAGE. IF A TRIBUNAL OF COMPETENT JURISDICTION DOES
 * NOT PERMIT THE DISCLAIMER OF DIRECT DAMAGES OR ANY OTHER DAMAGES, SYNAPTICS'
 * TOTAL CUMULATIVE LIABILITY TO ANY PARTY SHALL NOT EXCEED ONE HUNDRED U.S.
 * DOLLARS.
 */

/**
 * @file syna_tcm2_platform_i2c.c
 *
 * This file is the reference code of I2C module used for communicating with
 * Synaptics TouchCom device using I2C
 */

#include <linux/i2c.h>
#include <linux/of.h>

#include "syna_tcm2.h"
#include "syna_tcm2_cdev.h"
#include "syna_tcm2_platform.h"

#define I2C_MODULE_NAME "synaptics_tcm_i2c"

#define XFER_ATTEMPTS 5

static struct platform_device *syna_i2c_device;


/**
 * syna_request_managed_device()
 *
 * Request and return the device pointer for managed
 *
 * @param
 *     none.
 *
 * @return
 *     a device pointer allocated previously
 */
#if defined(DEV_MANAGED_API) || defined(USE_DRM_PANEL_NOTIFIER)
struct device *syna_request_managed_device(void)
{
	if (!syna_i2c_device)
		return NULL;

	return syna_i2c_device->dev.parent;
}
#endif


/**
 * syna_i2c_parse_gpiod()
 *
 * Resolve an optional GPIO descriptor from device tree using devres.
 *
 * @param
 *    [ in] dev:    I2C device owning the OF node
 *    [ in] con_id: DT property prefix without "-gpio"/"-gpios" suffix
 *    [out] desc:   resolved GPIO descriptor (NULL if absent)
 *
 * @return
 *    on success, 0; otherwise, negative error code.
 */
static int syna_i2c_parse_gpiod(struct device *dev, const char *con_id,
		struct gpio_desc **desc)
{
	struct gpio_desc *gpiod;

	gpiod = devm_gpiod_get_optional(dev, con_id, GPIOD_ASIS);
	if (IS_ERR(gpiod)) {
		LOGE("Fail to get GPIO %s (%ld)\n", con_id, PTR_ERR(gpiod));
		return PTR_ERR(gpiod);
	}

	*desc = gpiod;
	return 0;
}
/**
 * syna_i2c_free_gpios()
 *
 * GPIO descriptors are devres-managed on the I2C device; nothing to manually
 * free here.
 */
static int syna_i2c_free_gpios(struct syna_hw_interface *hw_if)
{
	return 0;
}
/**
 * syna_i2c_config_gpios()
 *
 * Initialize direction and initial raw polarity of GPIOs defined in device tree
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_config_gpios(struct syna_hw_interface *hw_if)
{
	int retval;
	struct syna_hw_attn_data *attn = &hw_if->bdata_attn;
	struct syna_hw_rst_data *rst = &hw_if->bdata_rst;
	struct syna_hw_bus_data *bus = &hw_if->bdata_io;

	if (attn->irq_gpiod) {
		retval = gpiod_direction_input(attn->irq_gpiod);
		if (retval < 0) {
			LOGE("Fail to configure interrupt GPIO direction (%d)\n",
				retval);
			return retval;
		}
	}

	if (rst->reset_gpiod) {
		retval = gpiod_direction_output_raw(rst->reset_gpiod,
				!rst->reset_on_state);
		if (retval < 0) {
			LOGE("Fail to configure reset GPIO direction (%d)\n",
				retval);
			return retval;
		}
	}

	if (bus->switch_gpiod) {
		retval = gpiod_direction_output_raw(bus->switch_gpiod,
				bus->switch_state);
		if (retval < 0) {
			LOGE("Fail to configure switch GPIO direction (%d)\n",
				retval);
			return retval;
		}
	}

	return 0;
}

/**
 * syna_i2c_parse_dt()
 *
 * Parse and obtain board specific data from the device tree source file.
 * Keep the data in structure syna_tcm_hw_data for later using.
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] dev: device model
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
#ifdef CONFIG_OF
static int syna_i2c_parse_dt(struct syna_hw_interface *hw_if,
		struct device *dev)
{
	int retval;
	u32 value;
	struct property *prop;
	struct device_node *np = dev->of_node;
	const char *name;
	struct syna_hw_attn_data *attn = &hw_if->bdata_attn;
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;
	struct syna_hw_rst_data *rst = &hw_if->bdata_rst;
	struct syna_hw_bus_data *bus = &hw_if->bdata_io;

	retval = syna_i2c_parse_gpiod(dev, "synaptics,irq", &attn->irq_gpiod);
	if (retval < 0)
		return retval;
	if (attn->irq_gpiod)
		attn->irq_flags = SYNAPTICS_TCM_IRQF_TRIGGER_MASK;

	retval = of_property_read_u32(np, "synaptics,irq-on-state", &value);
	if (retval < 0)
		attn->irq_on_state = 0;
	else
		attn->irq_on_state = value;

	prop = of_find_property(np, "synaptics,power-supply", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,power-supply",
				&value);
		if (retval < 0) {
			LOGE("Fail to read power-supply property\n");
			return retval;
		}

		pwr->psu = value;

	} else {
		pwr->psu = (int)PSU_REGULATOR;
	}

	retval = of_property_read_string(np, "synaptics,avdd-name", &name);
	if (retval < 0)
		pwr->avdd_reg_name = NULL;
	else
		pwr->avdd_reg_name = name;

	retval = of_property_read_string(np, "synaptics,vdd-name", &name);
	if (retval < 0)
		pwr->vdd_reg_name = NULL;
	else
		pwr->vdd_reg_name = name;

	retval = syna_i2c_parse_gpiod(dev, "synaptics,vdd", &pwr->vdd_gpiod);
	if (retval < 0)
		return retval;

	retval = syna_i2c_parse_gpiod(dev, "synaptics,avdd", &pwr->avdd_gpiod);
	if (retval < 0)
		return retval;

	prop = of_find_property(np, "synaptics,power-on-state", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,power-on-state",
				&value);
		if (retval < 0) {
			LOGE("Fail to read power-on-state property\n");
			return retval;
		}

		pwr->power_on_state = value;

	} else {
		pwr->power_on_state = 0;
	}

	prop = of_find_property(np, "synaptics,power-delay-ms", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,power-delay-ms",
				&value);
		if (retval < 0) {
			LOGE("Fail to read power-delay-ms property\n");
			return retval;
		}

		pwr->power_delay_ms = value;

	} else {
		pwr->power_delay_ms = 0;
	}

	retval = syna_i2c_parse_gpiod(dev, "synaptics,reset", &rst->reset_gpiod);
	if (retval < 0)
		return retval;

	prop = of_find_property(np, "synaptics,reset-on-state", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,reset-on-state",
				&value);
		if (retval < 0) {
			LOGE("Fail to read reset-on-state property\n");
			return retval;
		}

		rst->reset_on_state = value;

	} else {
		rst->reset_on_state = 0;
	}

	prop = of_find_property(np, "synaptics,reset-active-ms", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,reset-active-ms",
				&value);
		if (retval < 0) {
			LOGE("Fail to read reset-active-ms property\n");
			return retval;
		}

		rst->reset_active_ms = value;

	} else {
		rst->reset_active_ms = 0;
	}

	prop = of_find_property(np, "synaptics,reset-delay-ms", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,reset-delay-ms",
				&value);
		if (retval < 0) {
			LOGE("Fail to read reset-delay-ms property\n");
			return retval;
		}

		rst->reset_delay_ms = value;

	} else {
		rst->reset_delay_ms = 0;
	}

	retval = syna_i2c_parse_gpiod(dev, "synaptics,io-switch",
			&bus->switch_gpiod);
	if (retval < 0)
		return retval;

	prop = of_find_property(np, "synaptics,io-switch", NULL);
	if (prop && prop->length) {
		retval = of_property_read_u32(np, "synaptics,io-switch",
				&value);
		if (retval < 0) {
			LOGE("Fail to read io-switch property\n");
			return retval;
		}

		bus->switch_state = value;

	} else {
		bus->switch_state = 1;
	}

	return 0;
}
#endif

/**
 * syna_i2c_read()
 *
 * TouchCom over I2C uses the normal I2C addressing and transaction direction
 * mechanisms to select the device and retrieve the data.
 *
 * @param
 *    [ in] hw_if:   the handle of hw interface
 *    [out] rd_data: buffer for storing data retrieved from device
 *    [ in] rd_len: number of bytes retrieved from device
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_read(struct syna_hw_interface *hw_if,
		unsigned char *rd_data, unsigned int rd_len)
{
	int retval;
	unsigned int attempt;
	struct i2c_msg msg;
	struct i2c_client *i2c = hw_if->pdev;
	struct syna_hw_bus_data *bus = &hw_if->bdata_io;

	if (!i2c) {
		LOGE("Invalid bus io device\n");
		return -ENXIO;
	}

	syna_pal_mutex_lock(&bus->io_mutex);

	if ((rd_len & 0xffff) == 0xffff) {
		LOGE("Invalid read length 0x%X\n", (rd_len & 0xffff));
		retval = -EINVAL;
		goto exit;
	}

	msg.addr = i2c->addr;
	msg.flags = I2C_M_RD;
	msg.len = rd_len;
	msg.buf = rd_data;

	for (attempt = 0; attempt < XFER_ATTEMPTS; attempt++) {
		retval = i2c_transfer(i2c->adapter, &msg, 1);
		if (retval == 1) {
			retval = rd_len;
			goto exit;
		}
		LOGE("Transfer attempt %d failed\n", attempt + 1);

		if (attempt + 1 == XFER_ATTEMPTS) {
			retval = -EIO;
			goto exit;
		}

		syna_pal_sleep_ms(20);
	}

exit:
	syna_pal_mutex_unlock(&bus->io_mutex);

	return retval;
}

/**
 * syna_i2c_write()
 *
 * TouchCom over I2C uses the normal I2C addressing and transaction direction
 * mechanisms to select the device and send the data to the device.
 *
 * @param
 *    [ in] hw_if:   the handle of hw interface
 *    [ in] wr_data: written data
 *    [ in] wr_len: length of written data in bytes
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_write(struct syna_hw_interface *hw_if,
		unsigned char *wr_data, unsigned int wr_len)
{
	int retval;
	unsigned int attempt;
	struct i2c_msg msg;
	struct i2c_client *i2c = hw_if->pdev;
	struct syna_hw_bus_data *bus = &hw_if->bdata_io;

	if (!i2c) {
		LOGE("Invalid bus io device\n");
		return -ENXIO;
	}

	syna_pal_mutex_lock(&bus->io_mutex);

	if ((wr_len & 0xffff) == 0xffff) {
		LOGE("Invalid write length 0x%X\n", (wr_len & 0xffff));
		retval = -EINVAL;
		goto exit;
	}

	msg.addr = i2c->addr;
	msg.flags = 0;
	msg.len = wr_len;
	msg.buf = wr_data;

	for (attempt = 0; attempt < XFER_ATTEMPTS; attempt++) {
		retval = i2c_transfer(i2c->adapter, &msg, 1);
		if (retval == 1) {
			retval = wr_len;
			goto exit;
		}
		LOGE("Transfer attempt %d failed\n", attempt + 1);

		if (attempt + 1 == XFER_ATTEMPTS) {
			retval = -EIO;
			goto exit;
		}

		syna_pal_sleep_ms(20);
	}

exit:
	syna_pal_mutex_unlock(&bus->io_mutex);

	return retval;
}

/**
 * syna_i2c_hw_reset()
 *
 * Toggle the hardware gpio pin to perform the chip reset
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *
 * @return
 *     none.
 */
static void syna_i2c_hw_reset(struct syna_hw_interface *hw_if, int reset_delay_ms)
{
	struct syna_hw_rst_data *rst = &hw_if->bdata_rst;

	if (!rst->reset_gpiod)
		return;

	LOGD("Prepare to toggle reset, hold:%d delay:%d\n",
		rst->reset_active_ms, (reset_delay_ms != 0) ? reset_delay_ms : rst->reset_delay_ms);

	gpiod_set_raw_value(rst->reset_gpiod, (rst->reset_on_state & 0x01));
	syna_pal_sleep_ms(rst->reset_active_ms);
	gpiod_set_raw_value(rst->reset_gpiod, ((!rst->reset_on_state) & 0x01));
	if (reset_delay_ms != 0)
		syna_pal_sleep_ms(reset_delay_ms);
	else
		syna_pal_sleep_ms(rst->reset_delay_ms);
}


/**
 * syna_i2c_enable_pwr_gpio()
 *
 * Helper to enable power supply through GPIO
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] en:    '1' for enabling, and '0' for disabling
 *
 * @return
 *    none
 */
static int syna_i2c_enable_pwr_gpio(struct syna_hw_interface *hw_if,
		bool en)
{
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;
	int state = (en) ? pwr->power_on_state : !pwr->power_on_state;

	if (pwr->avdd_gpiod)
		gpiod_set_raw_value(pwr->avdd_gpiod, state);

	if (pwr->vdd_gpiod)
		gpiod_set_raw_value(pwr->vdd_gpiod, state);

	return 0;
}

/**
 * syna_i2c_enable_regulator()
 *
 * Enable or disable the regulator
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] en:    '1' for enabling, and '0' for disabling
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_enable_regulator(struct syna_hw_interface *hw_if,
		bool en)
{
	int retval;
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;
	struct regulator *vdd_reg = pwr->vdd_reg_dev;
	struct regulator *avdd_reg = pwr->avdd_reg_dev;

	if (!en) {
		retval = 0;
		goto disable_pwr_reg;
	}

	if (vdd_reg) {
		retval = regulator_enable(vdd_reg);
		if (retval < 0) {
			LOGE("Fail to enable vdd regulator\n");
			goto exit;
		}
	}

	if (avdd_reg) {
		retval = regulator_enable(avdd_reg);
		if (retval < 0) {
			LOGE("Fail to enable avdd regulator\n");
			goto disable_avdd_reg;
		}
	}

	return 0;

disable_pwr_reg:
	if (vdd_reg)
		regulator_disable(vdd_reg);

disable_avdd_reg:
	if (avdd_reg)
		regulator_disable(avdd_reg);

exit:
	return retval;
}

/**
 * syna_i2c_power_on()
 *
 * Power on touch controller through regulators or gpios for PWM
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] en:    '1' for powering on, and '0' for powering off
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_power_on(struct syna_hw_interface *hw_if,
		bool en)
{
	int retval;
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;

	LOGD("Prepare to power %s device through %s ...\n",
		(en) ? "on" : "off",
		(pwr->psu == PSU_REGULATOR) ? "regulator" : "gpio control");

	if (pwr->psu == PSU_REGULATOR)
		retval = syna_i2c_enable_regulator(hw_if, en);
	else
		retval = syna_i2c_enable_pwr_gpio(hw_if, en);

	if (retval < 0) {
		LOGE("Fail to power %s device\n", (en) ? "on" : "off");
		return retval;
	}

	syna_pal_sleep_ms(pwr->power_delay_ms);

	LOGI("Device power %s\n", (en) ? "on" : "off");

	return 0;
}

/**
 * syna_i2c_get_regulator()
 *
 * Acquire or release the regulator
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] get:   '1' for getting the regulator, and '0' for removing
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_get_regulator(struct syna_hw_interface *hw_if,
		bool get)
{
	int retval;
	struct device *dev = syna_i2c_device->dev.parent;
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;

	if (!get) {
		retval = 0;
		goto regulator_put;
	}

	if (pwr->vdd_reg_name != NULL && *pwr->vdd_reg_name != 0) {
#ifdef DEV_MANAGED_API
		pwr->vdd_reg_dev = devm_regulator_get(dev, pwr->vdd_reg_name);
#else /* Legacy API */
		pwr->vdd_reg_dev = regulator_get(dev, pwr->vdd_reg_name);
#endif
		if (IS_ERR((struct regulator *)pwr->vdd_reg_dev)) {
			LOGW("Vdd regulator is not ready\n");
			retval = PTR_ERR((struct regulator *)pwr->vdd_reg_dev);
			goto exit;
		}
	}

	if (pwr->avdd_reg_name != NULL && *pwr->avdd_reg_name != 0) {
#ifdef DEV_MANAGED_API
		pwr->avdd_reg_dev = devm_regulator_get(dev, pwr->avdd_reg_name);
#else /* Legacy API */
		pwr->avdd_reg_dev = regulator_get(dev, pwr->avdd_reg_name);
#endif
		if (IS_ERR((struct regulator *)pwr->avdd_reg_dev)) {
			LOGW("AVdd regulator is not ready\n");
			retval = PTR_ERR((struct regulator *)pwr->avdd_reg_dev);
			goto regulator_vdd_put;
		}
	}

	return 0;

regulator_put:
	if (pwr->vdd_reg_dev) {
#ifdef DEV_MANAGED_API
		devm_regulator_put(pwr->vdd_reg_dev);
#else /* Legacy API */
		regulator_put(pwr->vdd_reg_dev);
#endif
		pwr->vdd_reg_dev = NULL;
	}
regulator_vdd_put:
	if (pwr->avdd_reg_dev) {
#ifdef DEV_MANAGED_API
		devm_regulator_put(pwr->avdd_reg_dev);
#else /* Legacy API */
		regulator_put(pwr->avdd_reg_dev);
#endif
		pwr->avdd_reg_dev = NULL;
	}
exit:
	return retval;
}

/**
 * syna_i2c_config_psu()
 *
 * Initialize the power supply unit
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_config_psu(struct syna_hw_interface *hw_if)
{
	int retval;
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;

	if (pwr->psu != PSU_REGULATOR) {
		/* set up power gpio */
		if (pwr->vdd_gpiod) {
			retval = gpiod_direction_output_raw(pwr->vdd_gpiod,
					!pwr->power_on_state);
			if (retval < 0) {
				LOGE("Fail to configure vdd GPIO (%d)\n",
					retval);
				return retval;
			}
		}

		if (pwr->avdd_gpiod) {
			retval = gpiod_direction_output_raw(pwr->avdd_gpiod,
					!pwr->power_on_state);
			if (retval < 0) {
				LOGE("Fail to configure avdd GPIO (%d)\n",
					retval);
				return retval;
			}
		}
	} else {
		/* set up regulator */
		retval = syna_i2c_get_regulator(hw_if, true);
		if (retval < 0) {
			LOGE("Fail to configure regulators\n");
			return retval;
		}
	}

	return 0;
}

/**
 * syna_i2c_release_psu()
 *
 * Release the power supply unit
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_release_psu(struct syna_hw_interface *hw_if)
{
	struct syna_hw_pwr_data *pwr = &hw_if->bdata_pwr;

	if (pwr->psu == PSU_REGULATOR)
		syna_i2c_get_regulator(hw_if, false);

	return 0;
}

/**
 * syna_i2c_enable_irq()
 *
 * Enable or disable the handling of interrupt
 *
 * @param
 *    [ in] hw_if: the handle of hw interface
 *    [ in] en:    '1' for enabling, and '0' for disabling
 *
 * @return
 *    0 on success; otherwise, on error.
 */
static int syna_i2c_enable_irq(struct syna_hw_interface *hw_if,
		bool en)
{
	int retval = 0;
	struct syna_hw_attn_data *attn = &hw_if->bdata_attn;

	if (attn->irq_id == 0)
		return 0;

	syna_pal_mutex_lock(&attn->irq_en_mutex);

	/* enable the handling of interrupt */
	if (en) {
		if (attn->irq_enabled) {
			LOGD("Interrupt already enabled\n");
			retval = 0;
			goto exit;
		}

		enable_irq(attn->irq_id);
		attn->irq_enabled = true;

		LOGD("Interrupt enabled\n");
	}
	/* disable the handling of interrupt */
	else {
		if (!attn->irq_enabled) {
			LOGD("Interrupt already disabled\n");
			retval = 0;
			goto exit;
		}

		disable_irq_nosync(attn->irq_id);
		attn->irq_enabled = false;

		LOGD("Interrupt disabled\n");
	}

exit:
	syna_pal_mutex_unlock(&attn->irq_en_mutex);

	return retval;
}
/**
 * syna_hw_interface
 *
 * Provide the hardware specific settings in defaults.
 * Be noted the followings could be changed after .dtsi is parsed
 */
static struct syna_hw_interface syna_i2c_hw_if = {
	.bdata_io = {
		.type = BUS_TYPE_I2C,
		.rd_chunk_size = RD_CHUNK_SIZE,
		.wr_chunk_size = WR_CHUNK_SIZE,
	},
	.bdata_attn = {
		.irq_enabled = false,
		.irq_on_state = 0,
	},
	.bdata_rst = {
		.reset_on_state = 0,
		.reset_delay_ms = 200,
		.reset_active_ms = 20,
	},
	.bdata_pwr = {
		.power_on_state = 1,
		.power_delay_ms = 200,
	},
	.ops_power_on = syna_i2c_power_on,
	.ops_hw_reset = syna_i2c_hw_reset,
	.ops_read_data = syna_i2c_read,
	.ops_write_data = syna_i2c_write,
	.ops_enable_irq = syna_i2c_enable_irq,
};

/**
 * syna_i2c_probe()
 *
 * Prepare the specific hardware interface and register the platform i2c device
 *
 * @param
 *    [ in] i2c:    i2c client device
 *    [ in] dev_id: i2c device id
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static int syna_i2c_probe(struct i2c_client *i2c,
		const struct i2c_device_id *dev_id)
{
	int retval;
	struct syna_hw_attn_data *attn = &syna_i2c_hw_if.bdata_attn;
	struct syna_hw_bus_data *bus = &syna_i2c_hw_if.bdata_io;

	/* allocate an i2c platform device */
	syna_i2c_device = platform_device_alloc(PLATFORM_DRIVER_NAME, 0);
	if (!syna_i2c_device) {
		LOGE("Fail to allocate platform device\n");
		return -ENOMEM;
	}

#ifdef CONFIG_OF
	retval = syna_i2c_parse_dt(&syna_i2c_hw_if, &i2c->dev);
	if (retval < 0) {
		LOGE("Fail to parse dt (%d)\n", retval);
		platform_device_put(syna_i2c_device);
		return retval;
	}
#endif

	syna_pal_mutex_alloc(&attn->irq_en_mutex);
	syna_pal_mutex_alloc(&bus->io_mutex);

	/* keep the i/o device */
	syna_i2c_hw_if.pdev = i2c;

	syna_i2c_device->dev.parent = &i2c->dev;
	syna_i2c_device->dev.platform_data = &syna_i2c_hw_if;

	/* initialize power unit */
	retval = syna_i2c_config_psu(&syna_i2c_hw_if);
	if (retval < 0) {
		LOGE("Fail to config power unit\n");
		return retval;
	}

	/* initialize the gpio pins */
	retval = syna_i2c_config_gpios(&syna_i2c_hw_if);
	if (retval < 0) {
		LOGE("Fail to config gpio\n");
		return retval;
	}

	/* do i/o switch if defined */
	if (bus->switch_gpiod)
		gpiod_direction_output_raw(bus->switch_gpiod, bus->switch_state);

	/* register the i2c platform device */
	retval = platform_device_add(syna_i2c_device);
	if (retval < 0) {
		LOGE("Fail to add platform device\n");
		return retval;
	}

	return 0;
}

/**
 * syna_i2c_remove()
 *
 * Unregister the platform i2c device
 *
 * @param
 *    [ in] i2c: i2c client device
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
static void syna_i2c_remove(struct i2c_client *i2c)
{
	struct syna_hw_attn_data *attn = &syna_i2c_hw_if.bdata_attn;
	struct syna_hw_bus_data *bus = &syna_i2c_hw_if.bdata_io;

	/* release gpios */
	syna_i2c_free_gpios(&syna_i2c_hw_if);

	/* release power unit */
	syna_i2c_release_psu(&syna_i2c_hw_if);

	/* release mutex */
	syna_pal_mutex_free(&attn->irq_en_mutex);
	syna_pal_mutex_free(&bus->io_mutex);

	/* remove the platform device */
	syna_i2c_device->dev.platform_data = NULL;
	platform_device_unregister(syna_i2c_device);
}

/**
 * Describe an i2c device driver and its related declarations
 */
static const struct i2c_device_id syna_i2c_id_table[] = {
	{I2C_MODULE_NAME, 0},
	{},
};
MODULE_DEVICE_TABLE(i2c, syna_i2c_id_table);

#ifdef CONFIG_OF
static const struct of_device_id syna_i2c_of_match_table[] = {
	{
		.compatible = "synaptics,tcm-i2c",
	},
	{},
};
MODULE_DEVICE_TABLE(of, syna_i2c_of_match_table);
#else
#define syna_i2c_of_match_table NULL
#endif

static struct i2c_driver syna_i2c_driver = {
	.driver = {
		.name = I2C_MODULE_NAME,
		.owner = THIS_MODULE,
		.of_match_table = syna_i2c_of_match_table,
	},
	.probe = syna_i2c_probe,
	.remove = syna_i2c_remove,
	.id_table = syna_i2c_id_table,
};


/**
 * syna_hw_interface_init()
 *
 * Initialize the lower-level hardware interface module.
 * After returning, the handle of hw interface should be ready.
 *
 * @param
 *    void
 *
 * @return
 *    on success, 0; otherwise, negative value on error.
 */
int syna_hw_interface_init(void)
{
	return i2c_add_driver(&syna_i2c_driver);
}

/**
 * syna_hw_interface_exit()
 *
 * Delete the lower-level hardware interface module
 *
 * @param
 *    void
 *
 * @return
 *    none.
 */
void syna_hw_interface_exit(void)
{
	i2c_del_driver(&syna_i2c_driver);
}

MODULE_AUTHOR("Synaptics, Inc.");
MODULE_DESCRIPTION("Synaptics TouchCom I2C Bus Module");
MODULE_LICENSE("GPL v2");

