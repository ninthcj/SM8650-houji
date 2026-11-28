/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_MI_DISPLAY_PWRKEY_H
#define _LINUX_MI_DISPLAY_PWRKEY_H

typedef int (*mi_display_pwrkey_callback)(int);
void mi_display_pwrkey_callback_set(mi_display_pwrkey_callback cb);

#endif /* _LINUX_MI_DISPLAY_PWRKEY_H */
