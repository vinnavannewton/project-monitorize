#include "oblas16_afft.h"
#include "rs16.h"
#include <string.h>
static uint16_t fft_twiddles[16][32768];
static int fft_twiddles_initialized = 0;

static const uint16_t CANTOR_BASIS[16] = {0x0001, 0xacca, 0x3c0e, 0x163e, 0x4378, 0x029c, 0x0188, 0x007e,
                                          0x0426, 0x0022, 0x000c, 0x0092, 0x0012, 0x0006, 0x0002, 0x0800};

#include "oblas16.h"

#if defined(OBLAS_ARCH_ARM) || defined(OBLAS_ARCH_X86) || (defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector))
static uint8_t (*std_twiddles)[8][16];
#endif
#if defined(OBLAS_ARCH_X86)
static uint64_t (*gfni_twiddles)[4];
#endif

#if defined(OBLAS_ARCH_X86)
#include <immintrin.h>
#endif

static void oblas16_afft_bfly_fwd_ref(u16 *p0, u16 *p1, u16 twist, unsigned batch)
{
    for (unsigned j = 0; j < batch; j++) {
        u16 v0 = p0[j];
        u16 v1 = p1[j];
        u16 h0 = v0 ^ gf16_mul(twist, v1);
        p0[j] = h0;
        p1[j] = h0 ^ v1;
    }
}

static void oblas16_afft_bfly_inv_ref(u16 *p0, u16 *p1, u16 twist, unsigned batch)
{
    for (unsigned j = 0; j < batch; j++) {
        u16 h0 = p0[j];
        u16 h1 = p1[j];
        u16 v1 = h0 ^ h1;
        p0[j] = h0 ^ gf16_mul(twist, v1);
        p1[j] = v1;
    }
}

#define OBLAS16_AFFT_GENERATE_IMPL_X2(suffix, attr, VEC_TYPE, VEC_LOAD, VEC_STORE, VEC_XOR, VEC_MUL_INIT, VEC_MUL_CORE)            \
    attr static void oblas16_afft_bfly_fwd_##suffix(u16 *p0, u16 *p1, u16 twist, unsigned batch)                                   \
    {                                                                                                                              \
        VEC_MUL_INIT();                                                                                                            \
        VEC_TYPE *ap0 = (VEC_TYPE *)p0;                                                                                            \
        VEC_TYPE *ap1 = (VEC_TYPE *)p1;                                                                                            \
        unsigned fast_batch = batch & ~(sizeof(VEC_TYPE) - 1);                                                                     \
        unsigned j = 0;                                                                                                            \
        for (; j < fast_batch; j += sizeof(VEC_TYPE), ap0 += 2, ap1 += 2) {                                                        \
            VEC_TYPE v0_0 = VEC_LOAD(ap0);                                                                                         \
            VEC_TYPE v0_1 = VEC_LOAD(ap0 + 1);                                                                                     \
            VEC_TYPE v1_0 = VEC_LOAD(ap1);                                                                                         \
            VEC_TYPE v1_1 = VEC_LOAD(ap1 + 1);                                                                                     \
            VEC_TYPE prod_0, prod_1;                                                                                               \
            VEC_MUL_CORE(v1_0, v1_1, prod_0, prod_1);                                                                              \
            VEC_TYPE h0_0 = VEC_XOR(v0_0, prod_0);                                                                                 \
            VEC_TYPE h0_1 = VEC_XOR(v0_1, prod_1);                                                                                 \
            VEC_TYPE h1_0 = VEC_XOR(h0_0, v1_0);                                                                                   \
            VEC_TYPE h1_1 = VEC_XOR(h0_1, v1_1);                                                                                   \
            VEC_STORE(ap0, h0_0);                                                                                                  \
            VEC_STORE(ap0 + 1, h0_1);                                                                                              \
            VEC_STORE(ap1, h1_0);                                                                                                  \
            VEC_STORE(ap1 + 1, h1_1);                                                                                              \
        }                                                                                                                          \
        for (; j < batch; j++) {                                                                                                   \
            u16 v0 = p0[j];                                                                                                        \
            u16 v1 = p1[j];                                                                                                        \
            u16 h0 = v0 ^ gf16_mul(twist, v1);                                                                                     \
            p0[j] = h0;                                                                                                            \
            p1[j] = h0 ^ v1;                                                                                                       \
        }                                                                                                                          \
    }                                                                                                                              \
    attr static void oblas16_afft_bfly_inv_##suffix(u16 *p0, u16 *p1, u16 twist, unsigned batch)                                   \
    {                                                                                                                              \
        VEC_MUL_INIT();                                                                                                            \
        VEC_TYPE *ap = (VEC_TYPE *)p0;                                                                                             \
        VEC_TYPE *bp = (VEC_TYPE *)p1;                                                                                             \
        unsigned fast_batch = batch & ~(sizeof(VEC_TYPE) - 1);                                                                     \
        unsigned j = 0;                                                                                                            \
        for (; j < fast_batch; j += sizeof(VEC_TYPE), ap += 2, bp += 2) {                                                          \
            VEC_TYPE h0_0 = VEC_LOAD(ap);                                                                                          \
            VEC_TYPE h1_0 = VEC_LOAD(bp);                                                                                          \
            VEC_TYPE h0_1 = VEC_LOAD(ap + 1);                                                                                      \
            VEC_TYPE h1_1 = VEC_LOAD(bp + 1);                                                                                      \
            VEC_TYPE v1_0 = VEC_XOR(h0_0, h1_0);                                                                                   \
            VEC_TYPE v1_1 = VEC_XOR(h0_1, h1_1);                                                                                   \
            VEC_TYPE prod_0, prod_1;                                                                                               \
            VEC_MUL_CORE(v1_0, v1_1, prod_0, prod_1);                                                                              \
            VEC_TYPE v0_0 = VEC_XOR(h0_0, prod_0);                                                                                 \
            VEC_TYPE v0_1 = VEC_XOR(h0_1, prod_1);                                                                                 \
            VEC_STORE(ap, v0_0);                                                                                                   \
            VEC_STORE(ap + 1, v0_1);                                                                                               \
            VEC_STORE(bp, v1_0);                                                                                                   \
            VEC_STORE(bp + 1, v1_1);                                                                                               \
        }                                                                                                                          \
        for (; j < batch; j++) {                                                                                                   \
            u16 h0 = p0[j];                                                                                                        \
            u16 h1 = p1[j];                                                                                                        \
            u16 v1 = h0 ^ h1;                                                                                                      \
            p0[j] = h0 ^ gf16_mul(twist, v1);                                                                                      \
            p1[j] = v1;                                                                                                            \
        }                                                                                                                          \
    }

#if defined(OBLAS_ARCH_X86)

static inline void precompute_twist_std(u16 twist, uint8_t *t0l, uint8_t *t1l, uint8_t *t2l, uint8_t *t3l, uint8_t *t0h,
                                        uint8_t *t1h, uint8_t *t2h, uint8_t *t3h)
{
    for (int i = 0; i < 16; i++) {
        u16 p0 = gf16_mul(twist, i);
        u16 p1 = gf16_mul(twist, i << 4);
        u16 p2 = gf16_mul(twist, i << 8);
        u16 p3 = gf16_mul(twist, i << 12);
        t0l[i] = p0 & 0xFF;
        t0h[i] = p0 >> 8;
        t1l[i] = p1 & 0xFF;
        t1h[i] = p1 >> 8;
        t2l[i] = p2 & 0xFF;
        t2h[i] = p2 >> 8;
        t3l[i] = p3 & 0xFF;
        t3h[i] = p3 >> 8;
    }
}

static inline void build_4_matrices_gfni(u16 u, uint64_t *M_LL, uint64_t *M_HL, uint64_t *M_LH, uint64_t *M_HH)
{
    uint64_t x_LL = 0, x_HL = 0, x_LH = 0, x_HH = 0;
    uint32_t val = u;
    for (int k = 0; k < 8; k++) {
        x_LL |= ((uint64_t)(val & 0xFF)) << (k * 8);
        x_HL |= ((uint64_t)(val >> 8)) << (k * 8);
        val <<= 1;
        if (val & 0x10000)
            val ^= 0x1002D;
    }
    for (int k = 0; k < 8; k++) {
        x_LH |= ((uint64_t)(val & 0xFF)) << (k * 8);
        x_HH |= ((uint64_t)(val >> 8)) << (k * 8);
        val <<= 1;
        if (val & 0x10000)
            val ^= 0x1002D;
    }

    uint64_t t;
    t = (x_LL ^ (x_LL >> 7)) & 0x00AA00AA00AA00AAULL;
    x_LL = x_LL ^ t ^ (t << 7);
    t = (x_LL ^ (x_LL >> 14)) & 0x0000CCCC0000CCCCULL;
    x_LL = x_LL ^ t ^ (t << 14);
    t = (x_LL ^ (x_LL >> 28)) & 0x00000000F0F0F0F0ULL;
    x_LL = x_LL ^ t ^ (t << 28);
    *M_LL = __builtin_bswap64(x_LL);

    t = (x_HL ^ (x_HL >> 7)) & 0x00AA00AA00AA00AAULL;
    x_HL = x_HL ^ t ^ (t << 7);
    t = (x_HL ^ (x_HL >> 14)) & 0x0000CCCC0000CCCCULL;
    x_HL = x_HL ^ t ^ (t << 14);
    t = (x_HL ^ (x_HL >> 28)) & 0x00000000F0F0F0F0ULL;
    x_HL = x_HL ^ t ^ (t << 28);
    *M_HL = __builtin_bswap64(x_HL);

    t = (x_LH ^ (x_LH >> 7)) & 0x00AA00AA00AA00AAULL;
    x_LH = x_LH ^ t ^ (t << 7);
    t = (x_LH ^ (x_LH >> 14)) & 0x0000CCCC0000CCCCULL;
    x_LH = x_LH ^ t ^ (t << 14);
    t = (x_LH ^ (x_LH >> 28)) & 0x00000000F0F0F0F0ULL;
    x_LH = x_LH ^ t ^ (t << 28);
    *M_LH = __builtin_bswap64(x_LH);

    t = (x_HH ^ (x_HH >> 7)) & 0x00AA00AA00AA00AAULL;
    x_HH = x_HH ^ t ^ (t << 7);
    t = (x_HH ^ (x_HH >> 14)) & 0x0000CCCC0000CCCCULL;
    x_HH = x_HH ^ t ^ (t << 14);
    t = (x_HH ^ (x_HH >> 28)) & 0x00000000F0F0F0F0ULL;
    x_HH = x_HH ^ t ^ (t << 28);
    *M_HH = __builtin_bswap64(x_HH);
}

/* SSSE3 */
#define VEC_MUL_INIT_ssse3()                                                                                                       \
    __m128i T0_lo = _mm_loadu_si128((__m128i *)std_twiddles[twist][0]),                                                            \
            T1_lo = _mm_loadu_si128((__m128i *)std_twiddles[twist][1]),                                                            \
            T2_lo = _mm_loadu_si128((__m128i *)std_twiddles[twist][2]),                                                            \
            T3_lo = _mm_loadu_si128((__m128i *)std_twiddles[twist][3]);                                                            \
    __m128i T0_hi = _mm_loadu_si128((__m128i *)std_twiddles[twist][4]),                                                            \
            T1_hi = _mm_loadu_si128((__m128i *)std_twiddles[twist][5]),                                                            \
            T2_hi = _mm_loadu_si128((__m128i *)std_twiddles[twist][6]),                                                            \
            T3_hi = _mm_loadu_si128((__m128i *)std_twiddles[twist][7]);                                                            \
    __m128i mask_0f = _mm_set1_epi8(0x0F);

#define VEC_MUL_CORE_ssse3(x0, x1, res0, res1)                                                                                     \
    do {                                                                                                                           \
        __m128i tmp_mask = _mm_set1_epi16(0x00FF);                                                                                 \
        __m128i x_l = _mm_packus_epi16(_mm_and_si128(x0, tmp_mask), _mm_and_si128(x1, tmp_mask));                                  \
        __m128i x_h = _mm_packus_epi16(_mm_srli_epi16(x0, 8), _mm_srli_epi16(x1, 8));                                              \
        __m128i n0 = _mm_and_si128(x_l, mask_0f);                                                                                  \
        __m128i n1 = _mm_and_si128(_mm_srli_epi16(x_l, 4), mask_0f);                                                               \
        __m128i n2 = _mm_and_si128(x_h, mask_0f);                                                                                  \
        __m128i n3 = _mm_and_si128(_mm_srli_epi16(x_h, 4), mask_0f);                                                               \
        __m128i lo = _mm_xor_si128(_mm_xor_si128(_mm_shuffle_epi8(T0_lo, n0), _mm_shuffle_epi8(T1_lo, n1)),                        \
                                   _mm_xor_si128(_mm_shuffle_epi8(T2_lo, n2), _mm_shuffle_epi8(T3_lo, n3)));                       \
        __m128i hi = _mm_xor_si128(_mm_xor_si128(_mm_shuffle_epi8(T0_hi, n0), _mm_shuffle_epi8(T1_hi, n1)),                        \
                                   _mm_xor_si128(_mm_shuffle_epi8(T2_hi, n2), _mm_shuffle_epi8(T3_hi, n3)));                       \
        res0 = _mm_unpacklo_epi8(lo, hi);                                                                                          \
        res1 = _mm_unpackhi_epi8(lo, hi);                                                                                          \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(ssse3, __attribute__((target("ssse3"))), __m128i, _mm_loadu_si128, _mm_storeu_si128, _mm_xor_si128,
                              VEC_MUL_INIT_ssse3, VEC_MUL_CORE_ssse3)

/* SSSE3 GFNI */
#define VEC_MUL_INIT_ssse3_gfni()                                                                                                  \
    __m128i M_LL = _mm_set1_epi64x(gfni_twiddles[twist][0]);                                                                       \
    __m128i M_HL = _mm_set1_epi64x(gfni_twiddles[twist][1]);                                                                       \
    __m128i M_LH = _mm_set1_epi64x(gfni_twiddles[twist][2]);                                                                       \
    __m128i M_HH = _mm_set1_epi64x(gfni_twiddles[twist][3]);                                                                       \
    __m128i mask_00ff = _mm_set1_epi16(0x00FF);

#define VEC_MUL_CORE_ssse3_gfni(x0, x1, res0, res1)                                                                                \
    do {                                                                                                                           \
        __m128i x_l = _mm_packus_epi16(_mm_and_si128(x0, mask_00ff), _mm_and_si128(x1, mask_00ff));                                \
        __m128i x_h = _mm_packus_epi16(_mm_srli_epi16(x0, 8), _mm_srli_epi16(x1, 8));                                              \
        __m128i lo = _mm_xor_si128(_mm_gf2p8affine_epi64_epi8(x_l, M_LL, 0), _mm_gf2p8affine_epi64_epi8(x_h, M_LH, 0));            \
        __m128i hi = _mm_xor_si128(_mm_gf2p8affine_epi64_epi8(x_l, M_HL, 0), _mm_gf2p8affine_epi64_epi8(x_h, M_HH, 0));            \
        res0 = _mm_unpacklo_epi8(lo, hi);                                                                                          \
        res1 = _mm_unpackhi_epi8(lo, hi);                                                                                          \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(ssse3_gfni, __attribute__((target("ssse3,gfni"))), __m128i, _mm_loadu_si128, _mm_storeu_si128,
                              _mm_xor_si128, VEC_MUL_INIT_ssse3_gfni, VEC_MUL_CORE_ssse3_gfni)

#if !defined(_MSC_VER) || defined(__AVX2__)
/* AVX2 */
#define VEC_MUL_INIT_avx2()                                                                                                        \
    __m256i T0_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][0]));                               \
    __m256i T1_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][1]));                               \
    __m256i T2_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][2]));                               \
    __m256i T3_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][3]));                               \
    __m256i T0_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][4]));                               \
    __m256i T1_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][5]));                               \
    __m256i T2_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][6]));                               \
    __m256i T3_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)std_twiddles[twist][7]));                               \
    __m256i mask_0f = _mm256_set1_epi8(0x0F);

#define VEC_MUL_CORE_avx2(x0, x1, res0, res1)                                                                                      \
    do {                                                                                                                           \
        __m256i tmp_mask = _mm256_set1_epi16(0x00FF);                                                                              \
        __m256i x_l = _mm256_packus_epi16(_mm256_and_si256(x0, tmp_mask), _mm256_and_si256(x1, tmp_mask));                         \
        __m256i x_h = _mm256_packus_epi16(_mm256_srli_epi16(x0, 8), _mm256_srli_epi16(x1, 8));                                     \
        __m256i n0 = _mm256_and_si256(x_l, mask_0f);                                                                               \
        __m256i n1 = _mm256_and_si256(_mm256_srli_epi16(x_l, 4), mask_0f);                                                         \
        __m256i n2 = _mm256_and_si256(x_h, mask_0f);                                                                               \
        __m256i n3 = _mm256_and_si256(_mm256_srli_epi16(x_h, 4), mask_0f);                                                         \
        __m256i lo = _mm256_xor_si256(_mm256_xor_si256(_mm256_shuffle_epi8(T0_lo, n0), _mm256_shuffle_epi8(T1_lo, n1)),            \
                                      _mm256_xor_si256(_mm256_shuffle_epi8(T2_lo, n2), _mm256_shuffle_epi8(T3_lo, n3)));           \
        __m256i hi = _mm256_xor_si256(_mm256_xor_si256(_mm256_shuffle_epi8(T0_hi, n0), _mm256_shuffle_epi8(T1_hi, n1)),            \
                                      _mm256_xor_si256(_mm256_shuffle_epi8(T2_hi, n2), _mm256_shuffle_epi8(T3_hi, n3)));           \
        res0 = _mm256_unpacklo_epi8(lo, hi);                                                                                       \
        res1 = _mm256_unpackhi_epi8(lo, hi);                                                                                       \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(avx2, __attribute__((target("avx2"))), __m256i, _mm256_loadu_si256, _mm256_storeu_si256,
                              _mm256_xor_si256, VEC_MUL_INIT_avx2, VEC_MUL_CORE_avx2)

/* AVX2 GFNI */
#define VEC_MUL_INIT_avx2_gfni()                                                                                                   \
    __m256i M_LL = _mm256_set1_epi64x(gfni_twiddles[twist][0]);                                                                    \
    __m256i M_HL = _mm256_set1_epi64x(gfni_twiddles[twist][1]);                                                                    \
    __m256i M_LH = _mm256_set1_epi64x(gfni_twiddles[twist][2]);                                                                    \
    __m256i M_HH = _mm256_set1_epi64x(gfni_twiddles[twist][3]);                                                                    \
    __m256i mask_00ff = _mm256_set1_epi16(0x00FF);

#define VEC_MUL_CORE_avx2_gfni(x0, x1, res0, res1)                                                                                 \
    do {                                                                                                                           \
        __m256i x_l = _mm256_packus_epi16(_mm256_and_si256(x0, mask_00ff), _mm256_and_si256(x1, mask_00ff));                       \
        __m256i x_h = _mm256_packus_epi16(_mm256_srli_epi16(x0, 8), _mm256_srli_epi16(x1, 8));                                     \
        __m256i lo = _mm256_xor_si256(_mm256_gf2p8affine_epi64_epi8(x_l, M_LL, 0), _mm256_gf2p8affine_epi64_epi8(x_h, M_LH, 0));   \
        __m256i hi = _mm256_xor_si256(_mm256_gf2p8affine_epi64_epi8(x_l, M_HL, 0), _mm256_gf2p8affine_epi64_epi8(x_h, M_HH, 0));   \
        res0 = _mm256_unpacklo_epi8(lo, hi);                                                                                       \
        res1 = _mm256_unpackhi_epi8(lo, hi);                                                                                       \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(avx2_gfni, __attribute__((target("avx2,gfni"))), __m256i, _mm256_loadu_si256, _mm256_storeu_si256,
                              _mm256_xor_si256, VEC_MUL_INIT_avx2_gfni, VEC_MUL_CORE_avx2_gfni)

#endif
#if !defined(_MSC_VER) || defined(__AVX512F__)
/* AVX512 */
#define VEC_MUL_INIT_avx512()                                                                                                      \
    __m512i T0_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][0]));                                    \
    __m512i T1_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][1]));                                    \
    __m512i T2_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][2]));                                    \
    __m512i T3_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][3]));                                    \
    __m512i T0_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][4]));                                    \
    __m512i T1_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][5]));                                    \
    __m512i T2_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][6]));                                    \
    __m512i T3_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)std_twiddles[twist][7]));                                    \
    __m512i mask_0f = _mm512_set1_epi8(0x0F);

#define VEC_MUL_CORE_avx512(x0, x1, res0, res1)                                                                                    \
    do {                                                                                                                           \
        __m512i tmp_mask = _mm512_set1_epi16(0x00FF);                                                                              \
        __m512i x_l = _mm512_packus_epi16(_mm512_and_si512(x0, tmp_mask), _mm512_and_si512(x1, tmp_mask));                         \
        __m512i x_h = _mm512_packus_epi16(_mm512_srli_epi16(x0, 8), _mm512_srli_epi16(x1, 8));                                     \
        __m512i n0 = _mm512_and_si512(x_l, mask_0f);                                                                               \
        __m512i n1 = _mm512_and_si512(_mm512_srli_epi16(x_l, 4), mask_0f);                                                         \
        __m512i n2 = _mm512_and_si512(x_h, mask_0f);                                                                               \
        __m512i n3 = _mm512_and_si512(_mm512_srli_epi16(x_h, 4), mask_0f);                                                         \
        __m512i lo = _mm512_xor_si512(_mm512_xor_si512(_mm512_shuffle_epi8(T0_lo, n0), _mm512_shuffle_epi8(T1_lo, n1)),            \
                                      _mm512_xor_si512(_mm512_shuffle_epi8(T2_lo, n2), _mm512_shuffle_epi8(T3_lo, n3)));           \
        __m512i hi = _mm512_xor_si512(_mm512_xor_si512(_mm512_shuffle_epi8(T0_hi, n0), _mm512_shuffle_epi8(T1_hi, n1)),            \
                                      _mm512_xor_si512(_mm512_shuffle_epi8(T2_hi, n2), _mm512_shuffle_epi8(T3_hi, n3)));           \
        res0 = _mm512_unpacklo_epi8(lo, hi);                                                                                       \
        res1 = _mm512_unpackhi_epi8(lo, hi);                                                                                       \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(avx512, __attribute__((target("avx512f,avx512bw,avx512dq,avx512vl"))), __m512i, _mm512_loadu_si512,
                              _mm512_storeu_si512, _mm512_xor_si512, VEC_MUL_INIT_avx512, VEC_MUL_CORE_avx512)

/* AVX512 GFNI */
#define VEC_MUL_INIT_avx512_gfni()                                                                                                 \
    __m512i M_LL = _mm512_set1_epi64(gfni_twiddles[twist][0]);                                                                     \
    __m512i M_HL = _mm512_set1_epi64(gfni_twiddles[twist][1]);                                                                     \
    __m512i M_LH = _mm512_set1_epi64(gfni_twiddles[twist][2]);                                                                     \
    __m512i M_HH = _mm512_set1_epi64(gfni_twiddles[twist][3]);                                                                     \
    __m512i mask_00ff = _mm512_set1_epi16(0x00FF);

#define VEC_MUL_CORE_avx512_gfni(x0, x1, res0, res1)                                                                               \
    do {                                                                                                                           \
        __m512i x_l = _mm512_packus_epi16(_mm512_and_si512(x0, mask_00ff), _mm512_and_si512(x1, mask_00ff));                       \
        __m512i x_h = _mm512_packus_epi16(_mm512_srli_epi16(x0, 8), _mm512_srli_epi16(x1, 8));                                     \
        __m512i lo = _mm512_xor_si512(_mm512_gf2p8affine_epi64_epi8(x_l, M_LL, 0), _mm512_gf2p8affine_epi64_epi8(x_h, M_LH, 0));   \
        __m512i hi = _mm512_xor_si512(_mm512_gf2p8affine_epi64_epi8(x_l, M_HL, 0), _mm512_gf2p8affine_epi64_epi8(x_h, M_HH, 0));   \
        res0 = _mm512_unpacklo_epi8(lo, hi);                                                                                       \
        res1 = _mm512_unpackhi_epi8(lo, hi);                                                                                       \
    } while (0)

OBLAS16_AFFT_GENERATE_IMPL_X2(avx512_gfni, __attribute__((target("avx512f,avx512bw,avx512dq,avx512vl,gfni"))), __m512i,
                              _mm512_loadu_si512, _mm512_storeu_si512, _mm512_xor_si512, VEC_MUL_INIT_avx512_gfni,
                              VEC_MUL_CORE_avx512_gfni)

#endif
#endif /* OBLAS_ARCH_X86 */

#if defined(OBLAS_ARCH_ARM) && defined(__ARM_NEON)
#include <arm_neon.h>

#define VEC_MUL_INIT_neon()                                                                                                        \
    uint8x16_t T0_lo = vld1q_u8(std_twiddles[twist][0]), T1_lo = vld1q_u8(std_twiddles[twist][1]),                                 \
               T2_lo = vld1q_u8(std_twiddles[twist][2]), T3_lo = vld1q_u8(std_twiddles[twist][3]);                                 \
    uint8x16_t T0_hi = vld1q_u8(std_twiddles[twist][4]), T1_hi = vld1q_u8(std_twiddles[twist][5]),                                 \
               T2_hi = vld1q_u8(std_twiddles[twist][6]), T3_hi = vld1q_u8(std_twiddles[twist][7]);                                 \
    uint8x16_t mask_0f = vdupq_n_u8(0x0F);

#define VEC_MUL_CORE_neon(x0, x1, res0, res1)                                                                                      \
    do {                                                                                                                           \
        uint8x16x2_t input_v;                                                                                                      \
        input_v.val[0] = vreinterpretq_u8_u16(x0);                                                                                 \
        input_v.val[1] = vreinterpretq_u8_u16(x1);                                                                                 \
        uint8x16_t input_l = vuzp1q_u8(input_v.val[0], input_v.val[1]);                                                            \
        uint8x16_t input_h = vuzp2q_u8(input_v.val[0], input_v.val[1]);                                                            \
        uint8x16_t input_l_l = vandq_u8(input_l, mask_0f);                                                                         \
        uint8x16_t input_l_h = vandq_u8(vshrq_n_u8(input_l, 4), mask_0f);                                                          \
        uint8x16_t input_h_l = vandq_u8(input_h, mask_0f);                                                                         \
        uint8x16_t input_h_h = vandq_u8(vshrq_n_u8(input_h, 4), mask_0f);                                                          \
        uint8x16_t lo = veorq_u8(veorq_u8(vqtbl1q_u8(T0_lo, input_l_l), vqtbl1q_u8(T1_lo, input_l_h)),                             \
                                 veorq_u8(vqtbl1q_u8(T2_lo, input_h_l), vqtbl1q_u8(T3_lo, input_h_h)));                            \
        uint8x16_t hi = veorq_u8(veorq_u8(vqtbl1q_u8(T0_hi, input_l_l), vqtbl1q_u8(T1_hi, input_l_h)),                             \
                                 veorq_u8(vqtbl1q_u8(T2_hi, input_h_l), vqtbl1q_u8(T3_hi, input_h_h)));                            \
        uint8x16x2_t output_v;                                                                                                     \
        output_v.val[0] = lo;                                                                                                      \
        output_v.val[1] = hi;                                                                                                      \
        res0 = vreinterpretq_u16_u8(vzip1q_u8(lo, hi));                                                                            \
        res1 = vreinterpretq_u16_u8(vzip2q_u8(lo, hi));                                                                            \
    } while (0)

#define VEC_LOAD_neon(ptr) vld1q_u16((const uint16_t *)(ptr))
#define VEC_STORE_neon(ptr, val) vst1q_u16((uint16_t *)(ptr), val)

OBLAS16_AFFT_GENERATE_IMPL_X2(neon, , uint16x8_t, VEC_LOAD_neon, VEC_STORE_neon, veorq_u16, VEC_MUL_INIT_neon, VEC_MUL_CORE_neon)
#endif /* OBLAS_ARCH_ARM */

#if defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector)
#include <riscv_vector.h>

static void oblas16_afft_bfly_fwd_rvv(u16 *p0, u16 *p1, u16 twist, unsigned batch)
{
    if (twist == 0) {
        size_t vl;
        for (unsigned i = 0; i < batch; i += vl) {
            vl = __riscv_vsetvl_e16m1(batch - i);
            vuint16m1_t v0 = __riscv_vle16_v_u16m1(p0 + i, vl);
            vuint16m1_t v1 = __riscv_vle16_v_u16m1(p1 + i, vl);
            vuint16m1_t h1 = __riscv_vxor_vv_u16m1(v0, v1, vl);
            __riscv_vse16_v_u16m1(p1 + i, h1, vl);
        }
        return;
    }

    vuint8m1_t T0_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][0], 16);
    vuint8m1_t T1_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][1], 16);
    vuint8m1_t T2_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][2], 16);
    vuint8m1_t T3_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][3], 16);
    vuint8m1_t T0_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][4], 16);
    vuint8m1_t T1_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][5], 16);
    vuint8m1_t T2_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][6], 16);
    vuint8m1_t T3_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][7], 16);

    size_t vl;
    for (unsigned i = 0; i < batch; i += vl) {
        vl = __riscv_vsetvl_e8m1(batch - i);
        vuint8m1_t v0_low = __riscv_vlse8_v_u8m1((const uint8_t *)(p0 + i), 2, vl);
        vuint8m1_t v0_high = __riscv_vlse8_v_u8m1((const uint8_t *)(p0 + i) + 1, 2, vl);

        vuint8m1_t v1_low = __riscv_vlse8_v_u8m1((const uint8_t *)(p1 + i), 2, vl);
        vuint8m1_t v1_high = __riscv_vlse8_v_u8m1((const uint8_t *)(p1 + i) + 1, 2, vl);

        vuint8m1_t input_l_l = __riscv_vand_vx_u8m1(v1_low, 0x0F, vl);
        vuint8m1_t input_l_h = __riscv_vsrl_vx_u8m1(v1_low, 4, vl);
        vuint8m1_t input_h_l = __riscv_vand_vx_u8m1(v1_high, 0x0F, vl);
        vuint8m1_t input_h_h = __riscv_vsrl_vx_u8m1(v1_high, 4, vl);

        vuint8m1_t prod_lo_0 = __riscv_vrgather_vv_u8m1(T0_lo, input_l_l, vl);
        vuint8m1_t prod_lo_1 = __riscv_vrgather_vv_u8m1(T1_lo, input_l_h, vl);
        vuint8m1_t prod_lo_2 = __riscv_vrgather_vv_u8m1(T2_lo, input_h_l, vl);
        vuint8m1_t prod_lo_3 = __riscv_vrgather_vv_u8m1(T3_lo, input_h_h, vl);
        vuint8m1_t res_low = __riscv_vxor_vv_u8m1(__riscv_vxor_vv_u8m1(prod_lo_0, prod_lo_1, vl),
                                                  __riscv_vxor_vv_u8m1(prod_lo_2, prod_lo_3, vl), vl);

        vuint8m1_t prod_hi_0 = __riscv_vrgather_vv_u8m1(T0_hi, input_l_l, vl);
        vuint8m1_t prod_hi_1 = __riscv_vrgather_vv_u8m1(T1_hi, input_l_h, vl);
        vuint8m1_t prod_hi_2 = __riscv_vrgather_vv_u8m1(T2_hi, input_h_l, vl);
        vuint8m1_t prod_hi_3 = __riscv_vrgather_vv_u8m1(T3_hi, input_h_h, vl);
        vuint8m1_t res_high = __riscv_vxor_vv_u8m1(__riscv_vxor_vv_u8m1(prod_hi_0, prod_hi_1, vl),
                                                   __riscv_vxor_vv_u8m1(prod_hi_2, prod_hi_3, vl), vl);

        vuint8m1_t h0_low = __riscv_vxor_vv_u8m1(v0_low, res_low, vl);
        vuint8m1_t h0_high = __riscv_vxor_vv_u8m1(v0_high, res_high, vl);

        vuint8m1_t h1_low = __riscv_vxor_vv_u8m1(h0_low, v1_low, vl);
        vuint8m1_t h1_high = __riscv_vxor_vv_u8m1(h0_high, v1_high, vl);

        __riscv_vsse8_v_u8m1((uint8_t *)(p0 + i), 2, h0_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(p0 + i) + 1, 2, h0_high, vl);

        __riscv_vsse8_v_u8m1((uint8_t *)(p1 + i), 2, h1_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(p1 + i) + 1, 2, h1_high, vl);
    }
}

static void oblas16_afft_bfly_inv_rvv(u16 *p0, u16 *p1, u16 twist, unsigned batch)
{
    if (twist == 0) {
        size_t vl;
        for (unsigned i = 0; i < batch; i += vl) {
            vl = __riscv_vsetvl_e16m1(batch - i);
            vuint16m1_t h0 = __riscv_vle16_v_u16m1(p0 + i, vl);
            vuint16m1_t h1 = __riscv_vle16_v_u16m1(p1 + i, vl);
            vuint16m1_t v1 = __riscv_vxor_vv_u16m1(h0, h1, vl);
            __riscv_vse16_v_u16m1(p1 + i, v1, vl);
        }
        return;
    }

    vuint8m1_t T0_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][0], 16);
    vuint8m1_t T1_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][1], 16);
    vuint8m1_t T2_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][2], 16);
    vuint8m1_t T3_lo = __riscv_vle8_v_u8m1(std_twiddles[twist][3], 16);
    vuint8m1_t T0_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][4], 16);
    vuint8m1_t T1_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][5], 16);
    vuint8m1_t T2_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][6], 16);
    vuint8m1_t T3_hi = __riscv_vle8_v_u8m1(std_twiddles[twist][7], 16);

    size_t vl;
    for (unsigned i = 0; i < batch; i += vl) {
        vl = __riscv_vsetvl_e8m1(batch - i);
        vuint8m1_t h0_low = __riscv_vlse8_v_u8m1((const uint8_t *)(p0 + i), 2, vl);
        vuint8m1_t h0_high = __riscv_vlse8_v_u8m1((const uint8_t *)(p0 + i) + 1, 2, vl);

        vuint8m1_t h1_low = __riscv_vlse8_v_u8m1((const uint8_t *)(p1 + i), 2, vl);
        vuint8m1_t h1_high = __riscv_vlse8_v_u8m1((const uint8_t *)(p1 + i) + 1, 2, vl);

        vuint8m1_t v1_low = __riscv_vxor_vv_u8m1(h0_low, h1_low, vl);
        vuint8m1_t v1_high = __riscv_vxor_vv_u8m1(h0_high, h1_high, vl);

        vuint8m1_t input_l_l = __riscv_vand_vx_u8m1(v1_low, 0x0F, vl);
        vuint8m1_t input_l_h = __riscv_vsrl_vx_u8m1(v1_low, 4, vl);
        vuint8m1_t input_h_l = __riscv_vand_vx_u8m1(v1_high, 0x0F, vl);
        vuint8m1_t input_h_h = __riscv_vsrl_vx_u8m1(v1_high, 4, vl);

        vuint8m1_t prod_lo_0 = __riscv_vrgather_vv_u8m1(T0_lo, input_l_l, vl);
        vuint8m1_t prod_lo_1 = __riscv_vrgather_vv_u8m1(T1_lo, input_l_h, vl);
        vuint8m1_t prod_lo_2 = __riscv_vrgather_vv_u8m1(T2_lo, input_h_l, vl);
        vuint8m1_t prod_lo_3 = __riscv_vrgather_vv_u8m1(T3_lo, input_h_h, vl);
        vuint8m1_t res_low = __riscv_vxor_vv_u8m1(__riscv_vxor_vv_u8m1(prod_lo_0, prod_lo_1, vl),
                                                  __riscv_vxor_vv_u8m1(prod_lo_2, prod_lo_3, vl), vl);

        vuint8m1_t prod_hi_0 = __riscv_vrgather_vv_u8m1(T0_hi, input_l_l, vl);
        vuint8m1_t prod_hi_1 = __riscv_vrgather_vv_u8m1(T1_hi, input_l_h, vl);
        vuint8m1_t prod_hi_2 = __riscv_vrgather_vv_u8m1(T2_hi, input_h_l, vl);
        vuint8m1_t prod_hi_3 = __riscv_vrgather_vv_u8m1(T3_hi, input_h_h, vl);
        vuint8m1_t res_high = __riscv_vxor_vv_u8m1(__riscv_vxor_vv_u8m1(prod_hi_0, prod_hi_1, vl),
                                                   __riscv_vxor_vv_u8m1(prod_hi_2, prod_hi_3, vl), vl);

        vuint8m1_t v0_low = __riscv_vxor_vv_u8m1(h0_low, res_low, vl);
        vuint8m1_t v0_high = __riscv_vxor_vv_u8m1(h0_high, res_high, vl);

        __riscv_vsse8_v_u8m1((uint8_t *)(p0 + i), 2, v0_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(p0 + i) + 1, 2, v0_high, vl);

        __riscv_vsse8_v_u8m1((uint8_t *)(p1 + i), 2, v1_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(p1 + i) + 1, 2, v1_high, vl);
    }
}
#endif

void oblas16_afft_get_impl(struct oblas16_afft_impl *impl)
{
    impl->bfly_fwd = oblas16_afft_bfly_fwd_ref;
    impl->bfly_inv = oblas16_afft_bfly_inv_ref;
    impl->align_size = sizeof(void *);

#if defined(OBLAS_ARCH_X86)
#if !defined(_MSC_VER) || defined(__AVX512F__)
    if (__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("gfni")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_avx512_gfni;
        impl->bfly_inv = oblas16_afft_bfly_inv_avx512_gfni;
        impl->align_size = 64;
    } else if (__builtin_cpu_supports("avx512f")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_avx512;
        impl->bfly_inv = oblas16_afft_bfly_inv_avx512;
        impl->align_size = 64;
    } else
#endif
#if !defined(_MSC_VER) || defined(__AVX2__)
        if (__builtin_cpu_supports("avx2") && __builtin_cpu_supports("gfni")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_avx2_gfni;
        impl->bfly_inv = oblas16_afft_bfly_inv_avx2_gfni;
        impl->align_size = 32;
    } else if (__builtin_cpu_supports("avx2")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_avx2;
        impl->bfly_inv = oblas16_afft_bfly_inv_avx2;
        impl->align_size = 32;
    } else
#endif
        if (__builtin_cpu_supports("ssse3") && __builtin_cpu_supports("gfni")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_ssse3_gfni;
        impl->bfly_inv = oblas16_afft_bfly_inv_ssse3_gfni;
        impl->align_size = 16;
    } else if (__builtin_cpu_supports("ssse3")) {
        impl->bfly_fwd = oblas16_afft_bfly_fwd_ssse3;
        impl->bfly_inv = oblas16_afft_bfly_inv_ssse3;
        impl->align_size = 16;
    }
#elif defined(OBLAS_ARCH_ARM)
    impl->bfly_fwd = oblas16_afft_bfly_fwd_neon;
    impl->bfly_inv = oblas16_afft_bfly_inv_neon;
    impl->align_size = 16;
#elif defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector)
    impl->bfly_fwd = oblas16_afft_bfly_fwd_rvv;
    impl->bfly_inv = oblas16_afft_bfly_inv_rvv;
    impl->align_size = 16;
#endif
}

static void oblas16_afft_init_twiddles(void)
{
    if (fft_twiddles_initialized)
        return;

    uint16_t recur_val[16][16];
    for (int r = 0; r < 16; r++) {
        recur_val[0][r] = CANTOR_BASIS[r];
    }
    for (int k = 1; k < 16; k++) {
        for (int r = 0; r < 16 - k; r++) {
            uint16_t prev = recur_val[k - 1][r + 1];
            recur_val[k][r] = gf16_mul(prev, prev) ^ prev;
        }
    }

    for (int k = 0; k < 16; k++) {
        int max_b = 1 << (15 - k);
        for (int b = 0; b < max_b; b++) {
            uint16_t val = 0;
            for (int j = 0; j < 15 - k; j++) {
                if ((b >> j) & 1) {
                    val ^= recur_val[k][j + 1];
                }
            }
            fft_twiddles[k][b] = val;
        }
    }
    fft_twiddles_initialized = 1;
}

static int afft_impl_initialized = 0;

void oblas16_afft_init(void)
{
    if (afft_impl_initialized)
        return;

    oblas16_init();
    oblas16_afft_init_twiddles();
#if defined(OBLAS_ARCH_X86)
    if (__builtin_cpu_supports("gfni")) {
        gfni_twiddles = (uint64_t (*)[4])obl_alloc(65536, 32, 64);
        for (int i = 0; i < 65536; i++) {
            build_4_matrices_gfni(i, &gfni_twiddles[i][0], &gfni_twiddles[i][1], &gfni_twiddles[i][2], &gfni_twiddles[i][3]);
        }
    } else {
        std_twiddles = (uint8_t (*)[8][16])obl_alloc(65536, 128, 64);
        for (int i = 0; i < 65536; i++) {
            precompute_twist_std(i, std_twiddles[i][0], std_twiddles[i][1], std_twiddles[i][2], std_twiddles[i][3],
                                 std_twiddles[i][4], std_twiddles[i][5], std_twiddles[i][6], std_twiddles[i][7]);
        }
    }
#elif defined(OBLAS_ARCH_ARM) || (defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector))
    std_twiddles = (uint8_t (*)[8][16])obl_alloc(65536, 128, 64);
    for (int i = 0; i < 65536; i++) {
        for (int j = 0; j < 16; j++) {
            u16 p0 = gf16_mul(i, j);
            u16 p1 = gf16_mul(i, j << 4);
            u16 p2 = gf16_mul(i, j << 8);
            u16 p3 = gf16_mul(i, j << 12);
            std_twiddles[i][0][j] = p0 & 0xFF;
            std_twiddles[i][4][j] = p0 >> 8;
            std_twiddles[i][1][j] = p1 & 0xFF;
            std_twiddles[i][5][j] = p1 >> 8;
            std_twiddles[i][2][j] = p2 & 0xFF;
            std_twiddles[i][6][j] = p2 >> 8;
            std_twiddles[i][3][j] = p3 & 0xFF;
            std_twiddles[i][7][j] = p3 >> 8;
        }
    }
#endif
    afft_impl_initialized = 1;
}

static inline void afft_process_block(uint16_t *f, int half, int block_start, int batch, uint16_t twist, struct oblas16_impl *o16,
                                      struct oblas16_afft_impl *afft, int is_inv)
{
    int offset0 = block_start * batch;
    int offset1 = (block_start + half) * batch;
    if (twist == 0) {
        o16->axpy(&f[offset1], &f[offset0], 1, half * batch);
    } else {
        for (int i = 0; i < half; i++) {
            if (is_inv) {
                afft->bfly_inv(&f[offset0 + i * batch], &f[offset1 + i * batch], twist, batch);
            } else {
                afft->bfly_fwd(&f[offset0 + i * batch], &f[offset1 + i * batch], twist, batch);
            }
        }
    }
}

void oblas16_afft_fft(uint16_t *f, int log_n, int batch, uint8_t **needed, int chunk_idx, struct oblas16_impl *o16,
                      struct oblas16_afft_impl *afft)
{
    if (log_n <= 0)
        return;
    int target_working_set = 2 * 1024 * 1024;
    int split_blocks_limit = target_working_set / (batch * 2);
    int SPLIT = 0;
    if (split_blocks_limit > 1) {
        int limit = split_blocks_limit;
        while (limit > 1) {
            SPLIT++;
            limit >>= 1;
        }
    }
    if (SPLIT > 10)
        SPLIT = 10;
    if (SPLIT < 2)
        SPLIT = 2;
    if (log_n < SPLIT)
        SPLIT = log_n;

    for (int k = log_n - 1; k >= SPLIT; k--) {
        int half = 1 << k, step = 1 << (k + 1), num_blocks = 1 << (log_n - 1 - k);
        for (int b = 0; b < num_blocks; b++) {
            if (needed && !needed[k][b])
                continue;
            int global_b = chunk_idx * num_blocks + b;
            afft_process_block(f, half, b * step, batch, fft_twiddles[k][global_b], o16, afft, 0);
        }
    }

    int split_blocks = 1 << (log_n - SPLIT);
    for (int B = 0; B < split_blocks; B++) {
        for (int k = SPLIT - 1; k >= 0; k--) {
            int half = 1 << k, step = 1 << (k + 1), sub_blocks = 1 << (SPLIT - 1 - k);
            for (int sub_b = 0; sub_b < sub_blocks; sub_b++) {
                int b = B * sub_blocks + sub_b;
                if (needed && !needed[k][b])
                    continue;
                int global_b = chunk_idx * (1 << (log_n - 1 - k)) + b;
                afft_process_block(f, half, b * step, batch, fft_twiddles[k][global_b], o16, afft, 0);
            }
        }
    }
}

void oblas16_afft_ifft(uint16_t *f, int log_n, int batch, int max_input, int chunk_idx, struct oblas16_impl *o16,
                       struct oblas16_afft_impl *afft)
{
    if (log_n <= 0)
        return;
    int target_working_set = 1024 * 1024;
    int split_blocks_limit = target_working_set / (batch * 2);
    int SPLIT = 0;
    if (split_blocks_limit > 1) {
        int limit = split_blocks_limit;
        while (limit > 1) {
            SPLIT++;
            limit >>= 1;
        }
    }
    if (SPLIT > 10)
        SPLIT = 10;
    if (SPLIT < 2)
        SPLIT = 2;
    if (log_n < SPLIT)
        SPLIT = log_n;

    int split_blocks = 1 << (log_n - SPLIT);
    for (int B = 0; B < split_blocks; B++) {
        if (B * (1 << SPLIT) >= max_input)
            continue;

        for (int k = 0; k < SPLIT; k++) {
            int half = 1 << k, step = 1 << (k + 1), sub_blocks = 1 << (SPLIT - 1 - k);
            int active_b = (max_input + step - 1) >> (k + 1);
            for (int sub_b = 0; sub_b < sub_blocks; sub_b++) {
                int b = B * sub_blocks + sub_b;
                if (b >= active_b)
                    break;
                int global_b = chunk_idx * (1 << (log_n - 1 - k)) + b;
                afft_process_block(f, half, b * step, batch, fft_twiddles[k][global_b], o16, afft, 1);
            }
        }
    }

    for (int k = SPLIT; k < log_n; k++) {
        int half = 1 << k, step = 1 << (k + 1);
        int num_blocks = 1 << (log_n - 1 - k);
        int active_blocks = (max_input + step - 1) >> (k + 1);
        if (active_blocks < num_blocks)
            num_blocks = active_blocks;

        for (int b = 0; b < num_blocks; b++) {
            int global_b = chunk_idx * (1 << (log_n - 1 - k)) + b;
            afft_process_block(f, half, b * step, batch, fft_twiddles[k][global_b], o16, afft, 1);
        }
    }
}

static inline void gamma_process_block(uint16_t *arr, int half, int block_start, uint16_t twist, int is_fft)
{
    if (twist == 0) {
        for (int i = 0; i < half; i++) {
            arr[block_start + half + i] ^= arr[block_start + i];
        }
    } else {
        for (int i = 0; i < half; i++) {
            uint16_t p0 = arr[block_start + i];
            uint16_t p1 = arr[block_start + half + i];
            if (is_fft) {
                uint16_t h0 = p0 ^ gf16_mul(p1, twist);
                arr[block_start + i] = h0;
                arr[block_start + half + i] = p1 ^ h0;
            } else {
                uint16_t v1 = p0 ^ p1;
                arr[block_start + i] = p0 ^ gf16_mul(v1, twist);
                arr[block_start + half + i] = v1;
            }
        }
    }
}

uint16_t oblas16_afft_compute_gamma(int c, int log_M, int log_K_prime, int log_N, int c_out)
{
    if (log_M == log_N)
        return (c == c_out) ? 1 : 0;
    int log_macro_ifft = log_K_prime - log_M;
    int log_macro_fft = log_N - log_M;
    uint16_t *arr = (uint16_t *)obl_alloc(1, 32768 * sizeof(uint16_t), 64);
    if (!arr)
        return 0;
    arr[c] = 1;
    for (int k_macro = 0; k_macro < log_macro_ifft; k_macro++) {
        int k = k_macro + log_M;
        int half = 1 << k_macro;
        int step = 1 << (k_macro + 1);
        int num_blocks = 1 << (log_K_prime - 1 - k);
        for (int b = 0; b < num_blocks; b++) {
            gamma_process_block(arr, half, b * step, fft_twiddles[k][b], 0);
        }
    }
    for (int k_macro = log_macro_fft - 1; k_macro >= 0; k_macro--) {
        int k = k_macro + log_M;
        int half = 1 << k_macro;
        int step = 1 << (k_macro + 1);
        int num_blocks = 1 << (log_N - 1 - k);
        for (int b = 0; b < num_blocks; b++) {
            gamma_process_block(arr, half, b * step, fft_twiddles[k][b], 1);
        }
    }
    uint16_t ret = arr[c_out];
    obl_free(arr);
    return ret;
}
