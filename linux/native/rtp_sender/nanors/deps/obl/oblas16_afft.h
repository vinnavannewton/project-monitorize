#ifndef OBLAS16_AFFT_H
#define OBLAS16_AFFT_H

#include <stdint.h>
#include <stddef.h>

#include "oblas_common.h"

#ifdef __cplusplus
extern "C" {
#endif

struct oblas16_afft_impl {
    void (*bfly_fwd)(uint16_t *p0, uint16_t *p1, uint16_t twist, unsigned batch);
    void (*bfly_inv)(uint16_t *p0, uint16_t *p1, uint16_t twist, unsigned batch);
    size_t align_size;
};

void oblas16_afft_init(void);
void oblas16_afft_get_impl(struct oblas16_afft_impl *impl);

struct oblas16_impl;
void oblas16_afft_fft(uint16_t *f, int log_n, int batch, uint8_t **needed, int chunk_idx, struct oblas16_impl *o16,
                      struct oblas16_afft_impl *afft);
void oblas16_afft_ifft(uint16_t *f, int log_n, int batch, int max_input, int chunk_idx, struct oblas16_impl *o16,
                       struct oblas16_afft_impl *afft);
uint16_t oblas16_afft_compute_gamma(int c, int log_M, int log_K_prime, int log_N, int c_out);

#ifdef __cplusplus
}
#endif

#endif /* OBLAS16_AFFT_H */
