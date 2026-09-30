#define _GNU_SOURCE
#include "appliance.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <grp.h>

#define BOOT_DIR "/run/sendspin"
#define BOOT_FILE BOOT_DIR "/boot"
#define ROOT BOOT_DIR "/root"
#define PLAYER_UID 1000
struct boot_record { char magic[8]; struct appliance_config config; };
static int disabled(const char *message) {
    fprintf(stderr, "sendspin: playback disabled: %s\n", message);
    return -1;
}
static int read_all(int fd, void *buffer, size_t capacity, size_t *length) {
    size_t used = 0;
    while (used < capacity) {
        ssize_t n = read(fd, (char *)buffer + used, capacity - used);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) return -1;
        if (!n) { *length = used; return 0; }
        used += (size_t)n;
    }
    char extra;
    if (read(fd, &extra, 1) != 0) return -1;
    *length = used;
    return 0;
}
static int directory(const char *path, mode_t mode) {
    if (mkdir(path, mode) && errno != EEXIST) return -1;
    struct stat st;
    if (lstat(path, &st) || !S_ISDIR(st.st_mode) || st.st_uid != 0 || (st.st_mode & 0022)) return -1;
    return chmod(path, mode);
}
static int wait_audio(void) {
    /* Built-in HDA probe is asynchronous. Readiness only, never retry writes. */
    const struct timespec delay = { .tv_sec = 0, .tv_nsec = 100000000 };
    for (unsigned i = 0; i < 300; i++) {
        if (!access("/dev/snd/controlC0", F_OK) && !access("/dev/snd/pcmC0D1p", F_OK) &&
            !access("/proc/asound/card0/codec#0", F_OK)) return 0;
        if (nanosleep(&delay, NULL) && errno != EINTR) return -1;
    }
    return -1;
}
static int prepare(void) {
    char cmdline[APPLIANCE_CMDLINE_MAX + 1], error[160];
    struct boot_record record = { .magic = "SSBOOT1" };
    if (geteuid() != 0) return disabled("preparation requires root");
    if (directory(BOOT_DIR, 0700) || directory("/run/network", 0755) || directory(ROOT, 0700))
        return disabled("runtime directories unavailable");
    int fd = open("/proc/cmdline", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    size_t length;
    if (fd < 0) return disabled("kernel arguments unavailable");
    int result = read_all(fd, cmdline, APPLIANCE_CMDLINE_MAX, &length);
    close(fd);
    if (result || memchr(cmdline, 0, length)) return disabled("invalid kernel argument size");
    cmdline[length] = 0;
    if (appliance_parse(cmdline, &record.config, error, sizeof error)) return disabled(error);
    if (wait_audio()) return disabled("HDA readiness deadline exceeded");
    if (appliance_linux_audio(error, sizeof error)) return disabled(error);
    if (chown("/dev/snd/pcmC0D1p", PLAYER_UID, PLAYER_UID) || chmod("/dev/snd/pcmC0D1p", 0600))
        return disabled("optical PCM permissions failed");
    /* A fixed binary schema is init metadata, not a writable player config.
     * It is created once; failed/duplicate preparation can never replace it. */
    fd = open(BOOT_FILE, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return disabled("boot metadata creation failed");
    ssize_t n = write(fd, &record, sizeof record);
    int sync_result = fsync(fd);
    int close_result = close(fd);
    if (n != (ssize_t)sizeof record || sync_result || close_result) {
        unlink(BOOT_FILE);
        return disabled("boot metadata write failed");
    }
    puts("sendspin: boot configuration frozen; optical verified; analog muted");
    return 0;
}
static int read_boot(struct boot_record *record) {
    int fd = open(BOOT_FILE, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    size_t length = 0;
    int result = fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != 0 || st.st_gid != 0 ||
        (st.st_mode & 07777) != 0600 || st.st_nlink != 1 || st.st_size != (off_t)sizeof *record;
    if (!result) result = read_all(fd, record, sizeof *record, &length) || length != sizeof *record;
    close(fd);
    if (result || memcmp(record->magic, "SSBOOT1", 8)) return -1;
    /* The root-only schema must still contain terminators; no reinterpretation
     * or second percent decode on a supervised restart. */
    if (!memchr(record->config.server, 0, sizeof record->config.server) ||
        !memchr(record->config.port, 0, sizeof record->config.port) ||
        !memchr(record->config.id, 0, sizeof record->config.id) ||
        !memchr(record->config.name, 0, sizeof record->config.name)) return -1;
    return 0;
}
static int bind_readonly(const char *source, const char *target) {
    return mount(source, target, NULL, MS_BIND, NULL) ||
        mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, NULL);
}
static int device(const char *source, const char *target) {
    int fd = open(target, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    close(fd);
    return mount(source, target, NULL, MS_BIND, NULL);
}
static int sandbox(void) {
    if (unshare(CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWUTS) ||
        mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) ||
        mount("/appliance-root", ROOT, NULL, MS_BIND, NULL) ||
        mount("tmpfs", ROOT "/dev", "tmpfs", MS_NOSUID | MS_NOEXEC, "mode=0755,size=64k") ||
        mkdir(ROOT "/dev/snd", 0755) || chmod(ROOT "/dev/snd", 0755) ||
        device("/dev/snd/pcmC0D1p", ROOT "/dev/snd/pcmC0D1p") ||
        device("/dev/null", ROOT "/dev/null") ||
        device("/dev/urandom", ROOT "/dev/urandom") ||
        device("/dev/random", ROOT "/dev/random") ||
        bind_readonly("/run/network", ROOT "/etc/network") ||
        mount(NULL, ROOT "/dev", NULL, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NOEXEC, NULL) ||
        mount(NULL, ROOT, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, NULL) ||
        chdir(ROOT) || syscall(SYS_pivot_root, ".", "oldroot") || chdir("/") ||
        umount2("/oldroot", MNT_DETACH)) return -1;
    /* Keep direct serial stdout/stderr; no interactive console stdin. */
    int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (fd < 0 || dup2(fd, STDIN_FILENO) < 0) { if (fd >= 0) close(fd); return -1; }
    if (fd > 2) close(fd);
    int close_result = (int)syscall(SYS_close_range, 3u, ~0u, 0);
    if (close_result && errno != ENOSYS) return -1;
    if (close_result) {
        struct rlimit limit;
        if (getrlimit(RLIMIT_NOFILE, &limit) || limit.rlim_cur > 1048576) return -1;
        for (int i = 3; (rlim_t)i < limit.rlim_cur; i++) close(i);
    }
    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)) return -1;
    for (int cap = 0; cap <= CAP_LAST_CAP; cap++)
        if (prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)) return -1;
    if (setgroups(0, NULL) || setgid(PLAYER_UID) || setuid(PLAYER_UID)) return -1;
    struct __user_cap_header_struct header = { .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0 };
    struct __user_cap_data_struct caps[2] = {{0}, {0}};
    if (syscall(SYS_capset, &header, caps) || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return -1;
    return 0;
}
static int launch(void) {
    struct boot_record record;
    if (geteuid() != 0 || read_boot(&record)) {
        disabled("no valid frozen boot metadata");
        /* Invalid boots remain silent and off; native init delivers termination.
         * No restart loop, filesystem writes or player state. */
        pause();
        return 1;
    }
    char server[sizeof record.config.server + 16], port[32], id[160], name[160];
    snprintf(server, sizeof server, "--server=%s", record.config.server);
    snprintf(port, sizeof port, "--port=%s", record.config.port);
    snprintf(id, sizeof id, "--client-id=%s", record.config.id);
    snprintf(name, sizeof name, "--name=%s", record.config.name);
    puts("sendspin: launching isolated optical player (volume 20%, lead 100ms)");
    fflush(NULL);
    if (sandbox()) {
        disabled("player isolation failed");
        pause();
        return 1;
    }
    char *const argv[] = {"/usr/bin/sendspin", server, port, id, name, "--device=hw:0,1", NULL};
    char *const env[] = {"ALSA_CONFIG_PATH=/usr/share/alsa/alsa.conf", "RUST_LOG=info", "TZ=UTC", NULL};
    execve(argv[0], argv, env);
    disabled("player executable unavailable");
    pause();
    return 1;
}
int main(int argc, char **argv) {
    umask(0077);
    if (argc != 2) return disabled("expected fixed prepare or launch mode") ? 1 : 0;
    if (!strcmp(argv[1], "--prepare")) return prepare() ? 1 : 0;
    if (!strcmp(argv[1], "--launch")) return launch();
    return disabled("unknown launcher mode") ? 1 : 0;
}
