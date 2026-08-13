#ifndef __RS_H_
#define __RS_H_

#include <stdint.h>
#include <stddef.h>
#include "oblas_common.h"

#define DATA_SHARDS_MAX 255

#ifdef __cplusplus
extern "C" {
#endif

typedef struct _reed_solomon {
    int ds;
    int ps;
    int ts;
    void (*axpy)(uint8_t *a, uint8_t *b, uint8_t u, unsigned k);
    void (*scal)(uint8_t *a, uint8_t u, unsigned k);
    void (*axiy)(uint8_t *a, uint8_t *b, uint8_t u, unsigned k);
    size_t align_size;
    uint8_t p[];
} reed_solomon;

#define reed_solomon_bufsize(ds, ps) (sizeof(reed_solomon) + (ps) * (ds) + (ds) * (ds))
#define reed_solomon_reconstruct reed_solomon_decode

void reed_solomon_init(void);
reed_solomon *reed_solomon_new_static(void *buf, size_t len, int ds, int ps);
reed_solomon *reed_solomon_new(int data_shards, int parity_shards);
void reed_solomon_release(reed_solomon *rs);

int reed_solomon_encode(reed_solomon *rs, uint8_t **shards, int nr_shards, int bs);
int reed_solomon_decode(reed_solomon *rs, uint8_t **shards, uint8_t *marks, int nr_shards, int bs);

/*
 * nanors can process data of any length, but performance is significantly better
 * when your block sizes (bs) and allocated buffers are padded and aligned to the
 * optimal simd alignment for your cpu.
 */

/* returns padded/aligned memory */
void *reed_solomon_aligned_alloc(size_t size);
void reed_solomon_free(void *ptr);
/* returns the padded block size */
int reed_solomon_padded_size(int bs);

#ifdef __cplusplus
}
#endif

#endif
