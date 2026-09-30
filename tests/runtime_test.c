#include "appliance.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(condition) do { ++checks; if (!(condition)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #condition); exit(1); \
} } while (0)

static void parser_checks(void)
{
    struct appliance_config config;
    char error[256];
    const char *valid = "console=ttyS0 quiet sendspin.server=music.example "
        "sendspin.port=8927 sendspin.id=macbook-optical sendspin.name=MacBook%20Optical";
    CHECK(appliance_parse(valid, &config, error, sizeof(error)) == 0);
    CHECK(strcmp(config.server, "music.example") == 0);
    CHECK(strcmp(config.port, "8927") == 0);
    CHECK(strcmp(config.id, "macbook-optical") == 0);
    CHECK(strcmp(config.name, "MacBook Optical") == 0);
    CHECK(appliance_parse("sendspin.name=%E2%99%AB sendspin.id=optical "
        "sendspin.port=65535 sendspin.server=192.0.2.10", &config, error, sizeof(error)) == 0);
    CHECK(strcmp(config.name, "\xe2\x99\xab") == 0);
    CHECK(appliance_parse("sendspin.server=music.example sendspin.port=1 "
        "sendspin.id=optical sendspin.name=%24%28touch%20sentinel%29", &config,
        error, sizeof(error)) == 0);
    CHECK(strcmp(config.name, "$(touch sentinel)") == 0);
    CHECK(appliance_parse("sendspin.server=music.example sendspin.port=8927 "
        "sendspin.id=-optical sendspin.name=literal%2520space", &config,
        error, sizeof(error)) == 0);
    CHECK(strcmp(config.id, "-optical") == 0);
    CHECK(strcmp(config.name, "literal%20space") == 0);

    /* Rejected input must never become a serial escape or an echoed secret. */
    const char *invalid[] = {
        "", "quiet console=ttyS0",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical",
        "sendspin.server=music.example sendspin.id=optical sendspin.name=Name",
        "sendspin.port=8927 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=8927 sendspin.name=Name",
        "sendspin.server= sendspin.port=8927 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=0 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=65536 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=-1 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=89x sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id= sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%GG",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%00",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%0A",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%1B",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%7F",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%0D",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%C2%9B",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%C2%85",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%C2%9D",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%E2%80%AE",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%E2%81%A6",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%E2%80%A8",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%E2%80%A9",
        "sendspin.server=music.example/path sendspin.port=8927 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=user@music.example sendspin.port=8927 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example:8927 sendspin.port=8927 sendspin.id=optical sendspin.name=Name",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%C0%AF",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%ED%A0%80",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%F4%90%80%80",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=%E2%99",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=Name sendspin.name=Other",
        "sendspin.server=music.example sendspin.port=8927 sendspin.id=optical sendspin.name=Name sendspin.discover=1"
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        memset(error, 0, sizeof(error));
        CHECK(appliance_parse(invalid[i], &config, error, sizeof(error)) != 0);
        CHECK(error[0] != '\0');
        CHECK(strstr(error, "music.example") == NULL);
        CHECK(strchr(error, '\033') == NULL);
    }
    char oversized[APPLIANCE_CMDLINE_MAX + 2];
    memset(oversized, 'a', sizeof(oversized) - 1);
    oversized[sizeof(oversized) - 1] = '\0';
    CHECK(appliance_parse(oversized, &config, error, sizeof(error)) != 0);
}

struct audio_fixture {
    unsigned calls;
    unsigned fail_at;
    unsigned set_mask;
    unsigned read_mask;
    unsigned pcm_calls;
    unsigned cleanup_mask;
    int failed;
};
static const char *switches[] = {
    "Speaker Playback Switch", "Bass Speaker Playback Switch", "Headphone Playback Switch",
    "IEC958 Default PCM Playback Switch", "IEC958 Playback Switch"
};
static int step(struct audio_fixture *f)
{
    if (++f->calls == f->fail_at) { f->failed = 1; return -1; }
    return 0;
}
static int identify(void *context) { return step(context); }
static int switch_index(const char *name)
{
    for (unsigned i = 0; i < sizeof(switches) / sizeof(switches[0]); ++i)
        if (strcmp(name, switches[i]) == 0) return (int)i;
    return -1;
}
static int switch_set(void *context, const char *name, int enabled)
{
    struct audio_fixture *f = context;
    int i = switch_index(name);
    CHECK(i >= 0);
    if (f->failed) {
        CHECK(enabled == 0);
        f->cleanup_mask |= 1u << i;
        return step(f);
    }
    CHECK(enabled == (i >= 3));
    if (enabled) CHECK((f->read_mask & 7) == 7);
    int result = step(f);
    if (!result) f->set_mask |= 1u << i;
    return result;
}
static int switch_get(void *context, const char *name, int enabled)
{
    struct audio_fixture *f = context;
    int i = switch_index(name);
    CHECK(i >= 0);
    CHECK(enabled == (i >= 3));
    CHECK((f->set_mask & (1u << i)) != 0);
    int result = step(f);
    if (!result) f->read_mask |= 1u << i;
    return result;
}
static int pcm_verify(void *context)
{
    struct audio_fixture *f = context;
    CHECK(f->read_mask == 31);
    ++f->pcm_calls;
    return step(f);
}
static void audio_checks(void)
{
    const struct appliance_audio_ops ops = { identify, switch_set, switch_get, pcm_verify };
    struct audio_fixture fixture = {0};
    char error[256];
    CHECK(appliance_prepare(&ops, &fixture, error, sizeof(error)) == 0);
    CHECK(fixture.set_mask == 31);
    CHECK(fixture.read_mask == 31);
    CHECK(fixture.pcm_calls == 1);
    unsigned successful_calls = fixture.calls;
    /* Fail identity, each mixer write/readback, and PCM validation in turn.
       The public preparation seam must stop at that error: no alternate output. */
    for (unsigned failure = 1; failure <= successful_calls; ++failure) {
        fixture = (struct audio_fixture){ .fail_at = failure };
        error[0] = '\0';
        CHECK(appliance_prepare(&ops, &fixture, error, sizeof(error)) != 0);
        CHECK(error[0] != '\0');
        CHECK(fixture.cleanup_mask == 31);
        if (failure < successful_calls) CHECK(fixture.pcm_calls == 0);
    }
}
int main(void)
{
    parser_checks();
    audio_checks();
    printf("runtime: %u native behavioral assertions passed\n", checks);
    return 0;
}
