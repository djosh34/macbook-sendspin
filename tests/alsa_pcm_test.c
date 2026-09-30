#define _GNU_SOURCE
#include <alsa/asoundlib.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Exercise the real patched libasound at its filesystem boundary. Refusing the
 * PCM open deliberately avoids hardware ioctls while proving exactly which
 * device ALSA tried first. A control-card probe must fail this test. */
static unsigned pcm_opens, forbidden_opens;
static int dispatch_open(const char *path, int flags, mode_t mode, const char *symbol)
{
    if (strncmp(path, "/dev/snd/", 9) == 0) {
        if (strcmp(path, "/dev/snd/pcmC0D1p") == 0) ++pcm_opens;
        else {
            ++forbidden_opens;
            fprintf(stderr, "ALSA unexpectedly attempted %s\n", path);
        }
        errno = EACCES;
        return -1;
    }
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, symbol);
    if (!real_open) { fprintf(stderr, "cannot resolve libc %s\n", symbol); exit(1); }
    return real_open(path, flags, mode);
}
static mode_t open_mode(int flags, va_list args)
{
    if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE)
        return (mode_t)va_arg(args, int);
    return 0;
}
int open(const char *path, int flags, ...)
{
    va_list args;
    va_start(args, flags);
    mode_t mode = open_mode(flags, args);
    va_end(args);
    return dispatch_open(path, flags, mode, "open");
}
int open64(const char *path, int flags, ...)
{
    va_list args;
    va_start(args, flags);
    mode_t mode = open_mode(flags, args);
    va_end(args);
    return dispatch_open(path, flags, mode, "open64");
}
int main(void)
{
    const char *config = getenv("ALSA_CONFIG_PATH");
    if (!config || !*config) { fputs("ALSA_CONFIG_PATH must select player/alsa.conf\n", stderr); return 1; }
    snd_pcm_t *pcm = NULL;
    int result = snd_pcm_open(&pcm, "hw:0,1", SND_PCM_STREAM_PLAYBACK, 0);
    if (pcm) snd_pcm_close(pcm);
    snd_config_update_free_global();
    if (result != -EACCES || pcm_opens != 1 || forbidden_opens) {
        fprintf(stderr, "ALSA PCM bypass failed: result=%d PCM opens=%u forbidden opens=%u\n",
                result, pcm_opens, forbidden_opens);
        return 1;
    }
    puts("alsa: real libasound requested only exact optical PCM; no control probing");
    return 0;
}
