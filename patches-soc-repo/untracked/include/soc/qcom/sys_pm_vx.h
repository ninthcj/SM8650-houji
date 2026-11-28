/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 */

#ifndef __SOC_QCOM_SYS_PM_VX_H__
#define __SOC_QCOM_SYS_PM_VX_H__

#include <linux/kconfig.h>

#if IS_ENABLED(CONFIG_QTI_SYS_PM_VX)
void trigger_dump_enable(void);
void trigger_dump_disable(void);
#else
static inline void trigger_dump_enable(void) {}
static inline void trigger_dump_disable(void) {}
#endif

#endif /* __SOC_QCOM_SYS_PM_VX_H__ */
