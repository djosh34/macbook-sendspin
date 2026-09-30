#include "appliance.h"
#include <stdio.h>

static const char *const analog[] = {
    "Speaker Playback Switch", "Bass Speaker Playback Switch", "Headphone Playback Switch"
};
static const char *const digital[] = {
    "IEC958 Playback Switch", "IEC958 Default PCM Playback Switch"
};
int appliance_prepare(const struct appliance_audio_ops *ops, void *context, char *error, size_t size) {
    const char *message = "optical hardware identity verification failed";
    if (!ops || !ops->identify || !ops->switch_set || !ops->switch_get || !ops->pcm_verify || ops->identify(context)) goto failed;
    /* Mute every analog path before enabling optical. Verify every channel. */
    message = "analog mute preparation failed";
    for (size_t i = 0; i < sizeof analog / sizeof analog[0]; i++)
        if (ops->switch_set(context, analog[i], 0)) goto failed;
    for (size_t i = 0; i < sizeof analog / sizeof analog[0]; i++)
        if (ops->switch_get(context, analog[i], 0)) goto failed;
    message = "optical switch preparation failed";
    for (size_t i = 0; i < sizeof digital / sizeof digital[0]; i++)
        if (ops->switch_set(context, digital[i], 1)) goto failed;
    for (size_t i = 0; i < sizeof digital / sizeof digital[0]; i++)
        if (ops->switch_get(context, digital[i], 1)) goto failed;
    message = "optical PCM verification failed";
    if (ops->pcm_verify(context)) goto failed;
    if (size) error[0] = 0;
    return 0;
failed:
    /* Best effort silence on any failure; never launch with partial preparation. */
    if (ops && ops->switch_set) {
        for (size_t i = 0; i < sizeof digital / sizeof digital[0]; i++)
            (void)ops->switch_set(context, digital[i], 0);
        for (size_t i = 0; i < sizeof analog / sizeof analog[0]; i++)
            (void)ops->switch_set(context, analog[i], 0);
    }
    if (size) snprintf(error, size, "%s", message);
    return -1;
}
