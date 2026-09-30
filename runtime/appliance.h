#ifndef SENDSPIN_APPLIANCE_H
#define SENDSPIN_APPLIANCE_H
#include <stddef.h>
#define APPLIANCE_CMDLINE_MAX 8192
struct appliance_config {
    char server[254];
    char port[6];
    char id[129];
    char name[129];
};
/* Returns 0 on success; errors contain fixed diagnostics, never rejected input. */
int appliance_parse(const char *cmdline, struct appliance_config *out, char *error, size_t size);
/* Hardware identity and PCM callbacks must verify the exact CS4206/PCH optical path. */
struct appliance_audio_ops {
    int (*identify)(void *context);
    int (*switch_set)(void *context, const char *name, int enabled);
    int (*switch_get)(void *context, const char *name, int enabled);
    int (*pcm_verify)(void *context);
};
int appliance_prepare(const struct appliance_audio_ops *ops, void *context, char *error, size_t size);
int appliance_linux_audio(char *error, size_t size);
#endif
