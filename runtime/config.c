#define _POSIX_C_SOURCE 200809L
#include "appliance.h"
#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int fail(char *error, size_t size, const char *message) {
    if (size) snprintf(error, size, "%s", message);
    return -1;
}
static int hex(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
static int decode(const char *src, size_t len, char *dst, size_t capacity) {
    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)src[i];
        if (c == '%') {
            if (i + 2 >= len || hex(src[i + 1]) < 0 || hex(src[i + 2]) < 0) return -1;
            c = (unsigned char)((hex(src[i + 1]) << 4) | hex(src[i + 2]));
            i += 2;
        }
        /* Raw quotes have kernel parsing semantics; require % encoding instead. */
        else if (c == '"' || c == '\'') return -1;
        if (!c || j + 1 >= capacity) return -1;
        dst[j++] = (char)c;
    }
    dst[j] = 0;
    return j ? 0 : -1;
}
static int alnum_ascii(unsigned char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
}
static int hostname(const char *s) {
    unsigned char address[16];
    if (inet_pton(AF_INET, s, address) == 1 || inet_pton(AF_INET6, s, address) == 1) return 1;
    size_t total = strlen(s), label = 0;
    if (!total || total > 253) return 0;
    /* A numeric dotted value must be a real IPv4 address, not a DNS fallback. */
    int numeric = 1;
    for (size_t i = 0; i < total; i++) if ((s[i] < '0' || s[i] > '9') && s[i] != '.') numeric = 0;
    if (numeric) return 0;
    for (size_t i = 0; i < total; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '.') {
            if (!label || label > 63 || s[i - 1] == '-') return 0;
            label = 0;
        } else {
            if (!alnum_ascii(c) && c != '-') return 0;
            if (!label && c == '-') return 0;
            label++;
        }
    }
    return label && label <= 63 && s[total - 1] != '-';
}
static int display_name(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        uint32_t cp; unsigned count; uint32_t minimum;
        if (*p < 0x80) { cp = *p++; count = 0; minimum = 0; }
        else if (*p >= 0xc2 && *p <= 0xdf) { cp = *p++ & 0x1f; count = 1; minimum = 0x80; }
        else if (*p >= 0xe0 && *p <= 0xef) { cp = *p++ & 0x0f; count = 2; minimum = 0x800; }
        else if (*p >= 0xf0 && *p <= 0xf4) { cp = *p++ & 7; count = 3; minimum = 0x10000; }
        else return 0;
        for (unsigned i = 0; i < count; i++) {
            if ((*p & 0xc0) != 0x80) return 0;
            cp = (cp << 6) | (*p++ & 0x3f);
        }
        if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return 0;
        /* Reject controls, format/bidi controls, separators and noncharacters.
         * Cf ranges are conservative: invisible formatting is not needed in a
         * serial-visible appliance identity, including variation selectors. */
        if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f) || cp == 0xad ||
            (cp >= 0x600 && cp <= 0x605) || cp == 0x61c || cp == 0x6dd || cp == 0x70f ||
            (cp >= 0x890 && cp <= 0x891) || cp == 0x8e2 || cp == 0x180e ||
            (cp >= 0x200b && cp <= 0x200f) || (cp >= 0x2028 && cp <= 0x202e) ||
            (cp >= 0x2060 && cp <= 0x206f) || (cp >= 0xfe00 && cp <= 0xfe0f) || cp == 0xfeff ||
            (cp >= 0xfff9 && cp <= 0xfffb) || cp == 0x110bd || cp == 0x110cd ||
            (cp >= 0x13430 && cp <= 0x1343f) || (cp >= 0x1bca0 && cp <= 0x1bca3) ||
            (cp >= 0x1d173 && cp <= 0x1d17a) || (cp >= 0xe0000 && cp <= 0xe0fff) ||
            (cp >= 0xfdd0 && cp <= 0xfdef) || (cp & 0xffff) >= 0xfffe) return 0;
    }
    return 1;
}
int appliance_parse(const char *cmdline, struct appliance_config *out, char *error, size_t size) {
    struct appliance_config cfg = {0};
    const char *keys[] = {"sendspin.server", "sendspin.port", "sendspin.id", "sendspin.name"};
    char *dest[] = {cfg.server, cfg.port, cfg.id, cfg.name};
    size_t caps[] = {sizeof cfg.server, sizeof cfg.port, sizeof cfg.id, sizeof cfg.name};
    unsigned seen = 0;
    if (!cmdline || !out || strnlen(cmdline, APPLIANCE_CMDLINE_MAX + 1) > APPLIANCE_CMDLINE_MAX)
        return fail(error, size, "kernel arguments exceed limit");
    const char *p = cmdline;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\n') p++;
        const char *start = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\n') p++;
        size_t length = (size_t)(p - start);
        if (!length) break;
        if (length < 9 || memcmp(start, "sendspin.", 9)) {
            /* Reject quotes that could hide a mandatory token from the kernel. */
            if (memchr(start, '"', length) || memchr(start, '\'', length))
                return fail(error, size, "quoted kernel arguments are unsupported");
            continue;
        }
        int found = -1;
        for (int i = 0; i < 4; i++) {
            size_t n = strlen(keys[i]);
            if (length >= n && !memcmp(start, keys[i], n) && (length == n || start[n] == '=')) { found = i; break; }
        }
        if (found < 0) return fail(error, size, "unknown sendspin argument");
        if (seen & (1u << found)) return fail(error, size, "duplicate sendspin argument");
        size_t n = strlen(keys[found]);
        if (length <= n || decode(start + n + 1, length - n - 1, dest[found], caps[found]))
            return fail(error, size, "empty, oversized or malformed sendspin value");
        seen |= 1u << found;
    }
    if (seen != 15) return fail(error, size, "missing required sendspin argument");
    if (!hostname(cfg.server)) return fail(error, size, "invalid server hostname or address");
    unsigned port = 0;
    for (const char *s = cfg.port; *s; s++) {
        if (*s < '0' || *s > '9') return fail(error, size, "invalid server port");
        port = port * 10 + (unsigned)(*s - '0');
    }
    if (!port || port > 65535) return fail(error, size, "invalid server port");
    /* Canonical decimal avoids a downstream interpretation as octal. */
    snprintf(cfg.port, sizeof cfg.port, "%u", port);
    for (const char *s = cfg.id; *s; s++)
        if (!alnum_ascii((unsigned char)*s) && *s != '-' && *s != '_' && *s != '.')
            return fail(error, size, "invalid client id");
    if (!display_name(cfg.name)) return fail(error, size, "invalid UTF-8 or control in display name");
    *out = cfg;
    if (size) error[0] = 0;
    return 0;
}
