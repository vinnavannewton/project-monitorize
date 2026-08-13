#ifndef OBLAS16_H
#define OBLAS16_H

#include <stdint.h>
#include <stddef.h>

#include "oblas_common.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef uint16_t u16;

extern uint16_t GF16_LOG[65536];
extern uint16_t GF16_EXP[131072];

void oblas16_init(void);

static inline uint16_t gf16_mul(uint16_t a, uint16_t b)
{
    return (a && b) ? GF16_EXP[GF16_LOG[a] + GF16_LOG[b]] : 0;
}

static inline uint16_t gf16_inv(uint16_t a)
{
    return a ? GF16_EXP[65535 - GF16_LOG[a]] : 0;
}

struct oblas16_impl {
    void (*axpy)(u16 *a, const u16 *b, u16 u, unsigned k);
    void (*scal)(u16 *a, u16 u, unsigned k);
    void (*axiy)(u16 *a, const u16 *b, u16 u, unsigned k);
    size_t align_size;
};

void oblas16_get_impl(struct oblas16_impl *impl);
void oblas16_get_impl_by_name(struct oblas16_impl *impl, const char *name);

#ifdef __cplusplus
}
#endif

#endif // OBLAS16_H
