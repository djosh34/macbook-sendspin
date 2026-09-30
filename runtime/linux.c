#define _POSIX_C_SOURCE 200809L
#include "appliance.h"
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <sound/asound.h>

struct audio_context { int control; };
static int read_text(const char *path, char *buffer, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buffer, capacity - 1);
    char extra;
    int result = n < 0 || read(fd, &extra, 1) != 0;
    close(fd);
    if (result) return -1;
    buffer[n] = 0;
    return 0;
}
static int exact_file(const char *path, const char *expected) {
    char buffer[128];
    return read_text(path, buffer, sizeof buffer) || strcmp(buffer, expected);
}
static int identify(void *opaque) {
    struct audio_context *ctx = opaque;
    char codec[65536];
    if (exact_file("/proc/asound/card0/id", "PCH\n") ||
        exact_file("/sys/class/sound/card0/device/vendor", "0x8086\n") ||
        exact_file("/sys/class/sound/card0/device/device", "0x1e20\n") ||
        read_text("/proc/asound/card0/codec#0", codec, sizeof codec) ||
        !strstr(codec, "Codec: Cirrus Logic CS4206\n") ||
        !strstr(codec, "Vendor Id: 0x10134206\n") ||
        !strstr(codec, "Subsystem Id: 0x106b5200\n")) return -1;
    struct stat st;
    if (fstat(ctx->control, &st) || !S_ISCHR(st.st_mode) || major(st.st_rdev) != 116 || minor(st.st_rdev) != 0) return -1;
    struct snd_ctl_card_info info = {0};
    if (ioctl(ctx->control, SNDRV_CTL_IOCTL_CARD_INFO, &info) || info.card != 0 ||
        strcmp((char *)info.id, "PCH") || strcmp((char *)info.driver, "HDA-Intel")) return -1;
    return 0;
}
static int control_info(struct audio_context *ctx, const char *name, struct snd_ctl_elem_info *info) {
    memset(info, 0, sizeof *info);
    info->id.iface = SNDRV_CTL_ELEM_IFACE_MIXER;
    snprintf((char *)info->id.name, sizeof info->id.name, "%s", name);
    unsigned count = strstr(name, "IEC958") ? 1 : 2;
    if (ioctl(ctx->control, SNDRV_CTL_IOCTL_ELEM_INFO, info) ||
        info->type != SNDRV_CTL_ELEM_TYPE_BOOLEAN || info->count != count ||
        (info->access & SNDRV_CTL_ELEM_ACCESS_READWRITE) != SNDRV_CTL_ELEM_ACCESS_READWRITE ||
        (info->access & SNDRV_CTL_ELEM_ACCESS_INACTIVE)) return -1;
    return 0;
}
static int switch_set(void *opaque, const char *name, int enabled) {
    struct audio_context *ctx = opaque;
    struct snd_ctl_elem_info info;
    if (control_info(ctx, name, &info)) return -1;
    struct snd_ctl_elem_value value = {0};
    value.id = info.id;
    for (unsigned i = 0; i < info.count; i++) value.value.integer.value[i] = enabled;
    return ioctl(ctx->control, SNDRV_CTL_IOCTL_ELEM_WRITE, &value);
}
static int switch_get(void *opaque, const char *name, int enabled) {
    struct audio_context *ctx = opaque;
    struct snd_ctl_elem_info info;
    if (control_info(ctx, name, &info)) return -1;
    struct snd_ctl_elem_value value = {0};
    value.id = info.id;
    if (ioctl(ctx->control, SNDRV_CTL_IOCTL_ELEM_READ, &value)) return -1;
    for (unsigned i = 0; i < info.count; i++) if (value.value.integer.value[i] != enabled) return -1;
    return 0;
}
static int pcm_verify(void *opaque) {
    struct audio_context *ctx = opaque;
    struct snd_pcm_info info = {0};
    info.device = 1;
    info.stream = SNDRV_PCM_STREAM_PLAYBACK;
    if (ioctl(ctx->control, SNDRV_CTL_IOCTL_PCM_INFO, &info) || info.card != 0 || info.device != 1 ||
        info.stream != SNDRV_PCM_STREAM_PLAYBACK || strcmp((char *)info.id, "CS4206 Digital") ||
        strcmp((char *)info.name, "CS4206 Digital")) return -1;
    struct stat st;
    if (lstat("/dev/snd/pcmC0D1p", &st) || !S_ISCHR(st.st_mode) ||
        major(st.st_rdev) != 116 || minor(st.st_rdev) != 17) return -1;
    return 0;
}
int appliance_linux_audio(char *error, size_t size) {
    struct audio_context ctx = { .control = open("/dev/snd/controlC0", O_RDWR | O_CLOEXEC | O_NOFOLLOW) };
    if (ctx.control < 0) {
        if (size) snprintf(error, size, "HDA control unavailable");
        return -1;
    }
    const struct appliance_audio_ops ops = {identify, switch_set, switch_get, pcm_verify};
    int result = appliance_prepare(&ops, &ctx, error, size);
    close(ctx.control);
    return result;
}
