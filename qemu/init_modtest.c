#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static int finit_module_sys(int fd, const char *params, int flags) {
    return (int)syscall(SYS_finit_module, fd, params, flags);
}

static void mkdir_p(const char *path) {
    mkdir(path, 0755);
}

static void dump_file(const char *path, const char *tag) {
    FILE *f = fopen(path, "r");
    if (!f) {
        printf("[%s] cannot open %s: %s\n", tag, path, strerror(errno));
        return;
    }
    char buf[512];
    int count = 0;
    while (fgets(buf, sizeof(buf), f)) {
        printf("[%s] %s", tag, buf);
        count++;
    }
    fclose(f);
    printf("[%s] TOTAL_LINES=%d\n", tag, count);
}

int main(void) {
    mkdir_p("/proc");
    mkdir_p("/sys");
    mkdir_p("/dev");
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sysfs", "/sys", "sysfs", 0, NULL);
    mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);

    int cfd = open("/dev/console", O_RDWR);
    if (cfd >= 0) {
        dup2(cfd, 0);
        dup2(cfd, 1);
        dup2(cfd, 2);
        if (cfd > 2) close(cfd);
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);

    printf("\n=== [MODTEST] START QEMU ARM64 kCFI LIVE SMOKE TEST ===\n");
    dump_file("/proc/version", "KVER");

    FILE *list = fopen("/etc/modtest_order.txt", "r");
    if (!list) {
        printf("[MODTEST] FATAL: cannot open /etc/modtest_order.txt: %s\n", strerror(errno));
        sync();
        reboot(RB_POWER_OFF);
        return 1;
    }

    int total = 0, ok = 0, already = 0, hw_absent = 0, format_err = 0, other_err = 0;
    char line[512];
    while (fgets(line, sizeof(line), list)) {
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = '\0';
        if (len == 0 || line[0] == '#') continue;

        /* Format: <group> <path> */
        char *sp = strchr(line, ' ');
        if (!sp) continue;
        *sp = '\0';
        const char *group = line;
        const char *path = sp + 1;

        total++;
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            printf("[MODTEST] OPEN_ERR group=%s path=%s errno=%d (%s)\n",
                   group, path, errno, strerror(errno));
            other_err++;
            continue;
        }
        int rc = finit_module_sys(fd, "", 0);
        int err = errno;
        close(fd);

        if (rc == 0) {
            ok++;
            printf("[MODTEST] OK group=%s path=%s\n", group, path);
        } else if (err == EEXIST) {
            already++;
            printf("[MODTEST] ALREADY group=%s path=%s\n", group, path);
        } else if (err == ENOEXEC || err == EINVAL || err == EKEYREJECTED || err == EBADMSG) {
            /* Note: some QCOM drivers return -EINVAL when DT property is missing in QEMU virt,
             * whereas kernel ELF/CRC/vermagic errors print dmesg warnings and return ENOEXEC. */
            if (err == ENOEXEC || err == EKEYREJECTED || err == EBADMSG) {
                format_err++;
                printf("[MODTEST] FORMAT_ERR group=%s path=%s errno=%d (%s)\n",
                       group, path, err, strerror(err));
            } else {
                hw_absent++;
                printf("[MODTEST] INIT_ERR group=%s path=%s errno=%d (%s)\n",
                       group, path, err, strerror(err));
            }
        } else if (err == ENODEV || err == ENOENT || err == EIO || err == EOPNOTSUPP ||
                   err == ENXIO || err == ENOMEM || err == EAGAIN ||
                   err == EPERM || err == EACCES || err == ETIMEDOUT || err == EBUSY ||
                   err == EFAULT || err == ENOSYS) {
            hw_absent++;
            printf("[MODTEST] INIT_ERR group=%s path=%s errno=%d (%s)\n",
                   group, path, err, strerror(err));
        } else {
            other_err++;
            printf("[MODTEST] OTHER_ERR group=%s path=%s errno=%d (%s)\n",
                   group, path, err, strerror(err));
        }
    }
    fclose(list);

    printf("=== [MODTEST] LOADED_PROC_MODULES ===\n");
    dump_file("/proc/modules", "LSMOD");
    printf("=== [MODTEST] SUMMARY total=%d ok=%d already=%d init_err=%d format_err=%d other_err=%d ===\n",
           total, ok, already, hw_absent, format_err, other_err);
    printf("=== [MODTEST] DONE ===\n");
    fflush(stdout);
    usleep(200000);
    sync();
    reboot(RB_POWER_OFF);
    while (1) pause();
    return 0;
}
