#include "oblas16.h"
#include <string.h>

#if defined(OBLAS_ARCH_X86)
#include <immintrin.h>
#endif

uint16_t GF16_EXP[131072];
uint16_t GF16_LOG[65536];
static int gf16_initialized = 0;

void oblas16_init(void)
{
    if (gf16_initialized)
        return;
    uint32_t val = 1;
    for (int i = 0; i < 65535; i++) {
        GF16_EXP[i] = val;
        GF16_LOG[val] = i;
        val = (val << 1) ^ (-(int32_t)(val >> 15) & 0x1002d);
    }
    GF16_EXP[65535] = GF16_EXP[0];
    GF16_LOG[0] = 0; // log(0) is undefined, just set to 0
    for (int i = 0; i < 65535; i++) {
        GF16_EXP[65535 + i] = GF16_EXP[i];
    }
    gf16_initialized = 1;
}

static inline u16 gf16_mul_ref(u16 a, u16 b)
{
    if (a == 0 || b == 0)
        return 0;
    return GF16_EXP[GF16_LOG[a] + GF16_LOG[b]];
}

static void oblas16_axpy_ref(u16 *a, const u16 *b, u16 u, unsigned k)
{
    if (u == 0)
        return;
    if (u == 1) {
        for (unsigned j = 0; j < k; j++)
            a[j] ^= b[j];
        return;
    }
    for (unsigned j = 0; j < k; j++) {
        a[j] ^= gf16_mul_ref(b[j], u);
    }
}

static void oblas16_axiy_ref(u16 *a, const u16 *b, u16 u, unsigned k)
{
    if (u == 0) {
        memset(a, 0, k * sizeof(u16));
        return;
    }
    if (u == 1) {
        if (a != b)
            memcpy(a, b, k * sizeof(u16));
        return;
    }
    for (unsigned j = 0; j < k; j++) {
        a[j] = gf16_mul_ref(b[j], u);
    }
}

static void oblas16_scal_ref(u16 *a, u16 u, unsigned k)
{
    oblas16_axiy_ref(a, a, u, k);
}

#define OBL_NOOP(a, b) (b)

#define OBLAS16_SHUF_TEMPLATE(a, b, u, f, VEC_TYPE, VEC_LOAD, VEC_STORE, VEC_XOR, VEC_MUL_INIT, VEC_MUL_CORE, TAIL_STMT)           \
    do {                                                                                                                           \
        VEC_MUL_INIT();                                                                                                            \
        VEC_TYPE *ap = (VEC_TYPE *)a;                                                                                              \
        const VEC_TYPE *bp = (const VEC_TYPE *)b;                                                                                  \
        unsigned fast_batch = k & ~((2 * sizeof(VEC_TYPE) / sizeof(u16)) - 1);                                                     \
        unsigned j = 0;                                                                                                            \
        for (; j < fast_batch; j += (2 * sizeof(VEC_TYPE) / sizeof(u16)), ap += 2, bp += 2) {                                      \
            VEC_TYPE v0 = VEC_LOAD(bp);                                                                                            \
            VEC_TYPE v1 = VEC_LOAD(bp + 1);                                                                                        \
            VEC_TYPE res0, res1;                                                                                                   \
            VEC_MUL_CORE(v0, v1, res0, res1);                                                                                      \
            VEC_STORE(ap, f(VEC_LOAD(ap), res0));                                                                                  \
            VEC_STORE(ap + 1, f(VEC_LOAD(ap + 1), res1));                                                                          \
        }                                                                                                                          \
        TAIL_STMT;                                                                                                                 \
    } while (0)

#define OBLAS16_GENERATE_IMPL_X2(suffix, attr, VEC_TYPE, VEC_LOAD, VEC_STORE, VEC_XOR, VEC_MUL_INIT, VEC_MUL_CORE)                 \
    attr static void oblas16_axpy_##suffix(u16 *a, const u16 *b, u16 u, unsigned k)                                                \
    {                                                                                                                              \
        if (u == 0)                                                                                                                \
            return;                                                                                                                \
        if (u == 1) {                                                                                                              \
            unsigned fast_batch = k & ~((2 * sizeof(VEC_TYPE) / sizeof(u16)) - 1);                                                 \
            unsigned j = 0;                                                                                                        \
            VEC_TYPE *ap = (VEC_TYPE *)a;                                                                                          \
            const VEC_TYPE *bp = (const VEC_TYPE *)b;                                                                              \
            for (; j < fast_batch; j += (2 * sizeof(VEC_TYPE) / sizeof(u16)), ap += 2, bp += 2) {                                  \
                VEC_STORE(ap, VEC_XOR(VEC_LOAD(ap), VEC_LOAD(bp)));                                                                \
                VEC_STORE(ap + 1, VEC_XOR(VEC_LOAD(ap + 1), VEC_LOAD(bp + 1)));                                                    \
            }                                                                                                                      \
            for (; j < k; j++) {                                                                                                   \
                a[j] ^= b[j];                                                                                                      \
            }                                                                                                                      \
        } else {                                                                                                                   \
            OBLAS16_SHUF_TEMPLATE(a, b, u, VEC_XOR, VEC_TYPE, VEC_LOAD, VEC_STORE, VEC_XOR, VEC_MUL_INIT, VEC_MUL_CORE,            \
                                  oblas16_axpy_ref((u16 *)ap, (const u16 *)bp, u, k - j));                                         \
        }                                                                                                                          \
    }                                                                                                                              \
    attr static void oblas16_axiy_##suffix(u16 *a, const u16 *b, u16 u, unsigned k)                                                \
    {                                                                                                                              \
        if (u == 0) {                                                                                                              \
            memset(a, 0, k * sizeof(u16));                                                                                         \
        } else if (u == 1) {                                                                                                       \
            if (a != b)                                                                                                            \
                memcpy(a, b, k * sizeof(u16));                                                                                     \
        } else {                                                                                                                   \
            OBLAS16_SHUF_TEMPLATE(a, b, u, OBL_NOOP, VEC_TYPE, VEC_LOAD, VEC_STORE, VEC_XOR, VEC_MUL_INIT, VEC_MUL_CORE,           \
                                  oblas16_axiy_ref((u16 *)ap, (const u16 *)bp, u, k - j));                                         \
        }                                                                                                                          \
    }                                                                                                                              \
    attr static void oblas16_scal_##suffix(u16 *a, u16 u, unsigned k)                                                              \
    {                                                                                                                              \
        oblas16_axiy_##suffix(a, a, u, k);                                                                                         \
    }

static inline void precompute_twist_std(u16 twist, uint8_t *t0l, uint8_t *t1l, uint8_t *t2l, uint8_t *t3l, uint8_t *t0h,
                                        uint8_t *t1h, uint8_t *t2h, uint8_t *t3h)
{
    if (twist == 0) {
        memset(t0l, 0, 16);
        memset(t0h, 0, 16);
        memset(t1l, 0, 16);
        memset(t1h, 0, 16);
        memset(t2l, 0, 16);
        memset(t2h, 0, 16);
        memset(t3l, 0, 16);
        memset(t3h, 0, 16);
        return;
    }
    uint32_t log_twist = GF16_LOG[twist];
    t0l[0] = t0h[0] = 0;
    t1l[0] = t1h[0] = 0;
    t2l[0] = t2h[0] = 0;
    t3l[0] = t3h[0] = 0;
    for (int i = 1; i < 16; i++) {
        u16 p0 = GF16_EXP[log_twist + GF16_LOG[i]];
        u16 p1 = GF16_EXP[log_twist + GF16_LOG[i << 4]];
        u16 p2 = GF16_EXP[log_twist + GF16_LOG[i << 8]];
        u16 p3 = GF16_EXP[log_twist + GF16_LOG[i << 12]];
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

#if defined(OBLAS_ARCH_X86)

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
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];                                                                                    \
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];                                                                                    \
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);                                                               \
    __m128i T0_lo = _mm_loadu_si128((__m128i *)t0l), T1_lo = _mm_loadu_si128((__m128i *)t1l),                                      \
            T2_lo = _mm_loadu_si128((__m128i *)t2l), T3_lo = _mm_loadu_si128((__m128i *)t3l);                                      \
    __m128i T0_hi = _mm_loadu_si128((__m128i *)t0h), T1_hi = _mm_loadu_si128((__m128i *)t1h),                                      \
            T2_hi = _mm_loadu_si128((__m128i *)t2h), T3_hi = _mm_loadu_si128((__m128i *)t3h);                                      \
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

OBLAS16_GENERATE_IMPL_X2(ssse3, __attribute__((target("ssse3"))), __m128i, _mm_loadu_si128, _mm_storeu_si128, _mm_xor_si128,
                         VEC_MUL_INIT_ssse3, VEC_MUL_CORE_ssse3)

/* SSSE3 GFNI */
#define VEC_MUL_INIT_ssse3_gfni()                                                                                                  \
    uint64_t m_ll, m_hl, m_lh, m_hh;                                                                                               \
    build_4_matrices_gfni(u, &m_ll, &m_hl, &m_lh, &m_hh);                                                                          \
    __m128i M_LL = _mm_set1_epi64x(m_ll);                                                                                          \
    __m128i M_HL = _mm_set1_epi64x(m_hl);                                                                                          \
    __m128i M_LH = _mm_set1_epi64x(m_lh);                                                                                          \
    __m128i M_HH = _mm_set1_epi64x(m_hh);                                                                                          \
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

OBLAS16_GENERATE_IMPL_X2(ssse3_gfni, __attribute__((target("ssse3,gfni"))), __m128i, _mm_loadu_si128, _mm_storeu_si128,
                         _mm_xor_si128, VEC_MUL_INIT_ssse3_gfni, VEC_MUL_CORE_ssse3_gfni)

#if !defined(_MSC_VER) || defined(__AVX2__)
/* AVX2 */
#define VEC_MUL_INIT_avx2()                                                                                                        \
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];                                                                                    \
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];                                                                                    \
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);                                                               \
    __m256i T0_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t0l));                                                  \
    __m256i T1_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t1l));                                                  \
    __m256i T2_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t2l));                                                  \
    __m256i T3_lo = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t3l));                                                  \
    __m256i T0_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t0h));                                                  \
    __m256i T1_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t1h));                                                  \
    __m256i T2_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t2h));                                                  \
    __m256i T3_hi = _mm256_broadcastsi128_si256(_mm_loadu_si128((__m128i *)t3h));                                                  \
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

OBLAS16_GENERATE_IMPL_X2(avx2, __attribute__((target("avx2"))), __m256i, _mm256_loadu_si256, _mm256_storeu_si256, _mm256_xor_si256,
                         VEC_MUL_INIT_avx2, VEC_MUL_CORE_avx2)

/* AVX2 GFNI */
#define VEC_MUL_INIT_avx2_gfni()                                                                                                   \
    uint64_t m_ll, m_hl, m_lh, m_hh;                                                                                               \
    build_4_matrices_gfni(u, &m_ll, &m_hl, &m_lh, &m_hh);                                                                          \
    __m256i M_LL = _mm256_set1_epi64x(m_ll);                                                                                       \
    __m256i M_HL = _mm256_set1_epi64x(m_hl);                                                                                       \
    __m256i M_LH = _mm256_set1_epi64x(m_lh);                                                                                       \
    __m256i M_HH = _mm256_set1_epi64x(m_hh);                                                                                       \
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

OBLAS16_GENERATE_IMPL_X2(avx2_gfni, __attribute__((target("avx2,gfni"))), __m256i, _mm256_loadu_si256, _mm256_storeu_si256,
                         _mm256_xor_si256, VEC_MUL_INIT_avx2_gfni, VEC_MUL_CORE_avx2_gfni)

#endif
#if !defined(_MSC_VER) || defined(__AVX512F__)
/* AVX512 */
#define VEC_MUL_INIT_avx512()                                                                                                      \
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];                                                                                    \
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];                                                                                    \
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);                                                               \
    __m512i T0_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t0l));                                                       \
    __m512i T1_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t1l));                                                       \
    __m512i T2_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t2l));                                                       \
    __m512i T3_lo = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t3l));                                                       \
    __m512i T0_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t0h));                                                       \
    __m512i T1_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t1h));                                                       \
    __m512i T2_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t2h));                                                       \
    __m512i T3_hi = _mm512_broadcast_i32x4(_mm_loadu_si128((__m128i *)t3h));                                                       \
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

OBLAS16_GENERATE_IMPL_X2(avx512, __attribute__((target("avx512f,avx512bw,avx512dq,avx512vl"))), __m512i, _mm512_loadu_si512,
                         _mm512_storeu_si512, _mm512_xor_si512, VEC_MUL_INIT_avx512, VEC_MUL_CORE_avx512)

/* AVX512 GFNI */
#define VEC_MUL_INIT_avx512_gfni()                                                                                                 \
    uint64_t m_ll, m_hl, m_lh, m_hh;                                                                                               \
    build_4_matrices_gfni(u, &m_ll, &m_hl, &m_lh, &m_hh);                                                                          \
    __m512i M_LL = _mm512_set1_epi64(m_ll);                                                                                        \
    __m512i M_HL = _mm512_set1_epi64(m_hl);                                                                                        \
    __m512i M_LH = _mm512_set1_epi64(m_lh);                                                                                        \
    __m512i M_HH = _mm512_set1_epi64(m_hh);                                                                                        \
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

OBLAS16_GENERATE_IMPL_X2(avx512_gfni, __attribute__((target("avx512f,avx512bw,avx512dq,avx512vl,gfni"))), __m512i,
                         _mm512_loadu_si512, _mm512_storeu_si512, _mm512_xor_si512, VEC_MUL_INIT_avx512_gfni,
                         VEC_MUL_CORE_avx512_gfni)

#endif
#endif // OBLAS_ARCH_X86

#if defined(OBLAS_ARCH_ARM) && defined(__ARM_NEON)
#include <arm_neon.h>

#define VEC_MUL_INIT_neon()                                                                                                        \
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];                                                                                    \
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];                                                                                    \
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);                                                               \
    uint8x16_t T0_lo = vld1q_u8(t0l), T1_lo = vld1q_u8(t1l), T2_lo = vld1q_u8(t2l), T3_lo = vld1q_u8(t3l);                         \
    uint8x16_t T0_hi = vld1q_u8(t0h), T1_hi = vld1q_u8(t1h), T2_hi = vld1q_u8(t2h), T3_hi = vld1q_u8(t3h);                         \
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

OBLAS16_GENERATE_IMPL_X2(neon, , uint16x8_t, VEC_LOAD_neon, VEC_STORE_neon, veorq_u16, VEC_MUL_INIT_neon, VEC_MUL_CORE_neon)
#endif // OBLAS_ARCH_ARM

#if defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector)
#include <riscv_vector.h>

static void oblas16_axpy_rvv(u16 *a, const u16 *b, u16 u, unsigned k)
{
    if (u == 0)
        return;
    if (u == 1) {
        size_t vl;
        for (unsigned i = 0; i < k; i += vl) {
            vl = __riscv_vsetvl_e16m1(k - i);
            vuint16m1_t va = __riscv_vle16_v_u16m1(a + i, vl);
            vuint16m1_t vb = __riscv_vle16_v_u16m1(b + i, vl);
            vuint16m1_t res = __riscv_vxor_vv_u16m1(va, vb, vl);
            __riscv_vse16_v_u16m1(a + i, res, vl);
        }
        return;
    }
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);
    vuint8m1_t T0_lo = __riscv_vle8_v_u8m1(t0l, 16);
    vuint8m1_t T1_lo = __riscv_vle8_v_u8m1(t1l, 16);
    vuint8m1_t T2_lo = __riscv_vle8_v_u8m1(t2l, 16);
    vuint8m1_t T3_lo = __riscv_vle8_v_u8m1(t3l, 16);
    vuint8m1_t T0_hi = __riscv_vle8_v_u8m1(t0h, 16);
    vuint8m1_t T1_hi = __riscv_vle8_v_u8m1(t1h, 16);
    vuint8m1_t T2_hi = __riscv_vle8_v_u8m1(t2h, 16);
    vuint8m1_t T3_hi = __riscv_vle8_v_u8m1(t3h, 16);

    size_t vl;
    for (unsigned i = 0; i < k; i += vl) {
        vl = __riscv_vsetvl_e8m1(k - i);
        vuint8m1_t low = __riscv_vlse8_v_u8m1((const uint8_t *)(b + i), 2, vl);
        vuint8m1_t high = __riscv_vlse8_v_u8m1((const uint8_t *)(b + i) + 1, 2, vl);

        vuint8m1_t input_l_l = __riscv_vand_vx_u8m1(low, 0x0F, vl);
        vuint8m1_t input_l_h = __riscv_vsrl_vx_u8m1(low, 4, vl);
        vuint8m1_t input_h_l = __riscv_vand_vx_u8m1(high, 0x0F, vl);
        vuint8m1_t input_h_h = __riscv_vsrl_vx_u8m1(high, 4, vl);

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

        vuint8m1_t dest_low = __riscv_vlse8_v_u8m1((const uint8_t *)(a + i), 2, vl);
        vuint8m1_t dest_high = __riscv_vlse8_v_u8m1((const uint8_t *)(a + i) + 1, 2, vl);

        vuint8m1_t out_low = __riscv_vxor_vv_u8m1(dest_low, res_low, vl);
        vuint8m1_t out_high = __riscv_vxor_vv_u8m1(dest_high, res_high, vl);

        __riscv_vsse8_v_u8m1((uint8_t *)(a + i), 2, out_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(a + i) + 1, 2, out_high, vl);
    }
}

static void oblas16_axiy_rvv(u16 *a, const u16 *b, u16 u, unsigned k)
{
    if (u == 0) {
        memset(a, 0, k * sizeof(u16));
        return;
    }
    if (u == 1) {
        if (a != b)
            memcpy(a, b, k * sizeof(u16));
        return;
    }
    uint8_t t0l[16], t1l[16], t2l[16], t3l[16];
    uint8_t t0h[16], t1h[16], t2h[16], t3h[16];
    precompute_twist_std(u, t0l, t1l, t2l, t3l, t0h, t1h, t2h, t3h);
    vuint8m1_t T0_lo = __riscv_vle8_v_u8m1(t0l, 16);
    vuint8m1_t T1_lo = __riscv_vle8_v_u8m1(t1l, 16);
    vuint8m1_t T2_lo = __riscv_vle8_v_u8m1(t2l, 16);
    vuint8m1_t T3_lo = __riscv_vle8_v_u8m1(t3l, 16);
    vuint8m1_t T0_hi = __riscv_vle8_v_u8m1(t0h, 16);
    vuint8m1_t T1_hi = __riscv_vle8_v_u8m1(t1h, 16);
    vuint8m1_t T2_hi = __riscv_vle8_v_u8m1(t2h, 16);
    vuint8m1_t T3_hi = __riscv_vle8_v_u8m1(t3h, 16);

    size_t vl;
    for (unsigned i = 0; i < k; i += vl) {
        vl = __riscv_vsetvl_e8m1(k - i);
        vuint8m1_t low = __riscv_vlse8_v_u8m1((const uint8_t *)(b + i), 2, vl);
        vuint8m1_t high = __riscv_vlse8_v_u8m1((const uint8_t *)(b + i) + 1, 2, vl);

        vuint8m1_t input_l_l = __riscv_vand_vx_u8m1(low, 0x0F, vl);
        vuint8m1_t input_l_h = __riscv_vsrl_vx_u8m1(low, 4, vl);
        vuint8m1_t input_h_l = __riscv_vand_vx_u8m1(high, 0x0F, vl);
        vuint8m1_t input_h_h = __riscv_vsrl_vx_u8m1(high, 4, vl);

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

        __riscv_vsse8_v_u8m1((uint8_t *)(a + i), 2, res_low, vl);
        __riscv_vsse8_v_u8m1((uint8_t *)(a + i) + 1, 2, res_high, vl);
    }
}

static void oblas16_scal_rvv(u16 *a, u16 u, unsigned k)
{
    oblas16_axiy_rvv(a, a, u, k);
}
#endif

void oblas16_get_impl(struct oblas16_impl *impl)
{
    impl->axpy = oblas16_axpy_ref;
    impl->scal = oblas16_scal_ref;
    impl->axiy = oblas16_axiy_ref;
    impl->align_size = sizeof(void *);

#if defined(OBLAS_ARCH_X86)
#if !defined(_MSC_VER) || defined(__AVX512F__)
    if (__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("gfni")) {
        impl->axpy = oblas16_axpy_avx512_gfni;
        impl->scal = oblas16_scal_avx512_gfni;
        impl->axiy = oblas16_axiy_avx512_gfni;
        impl->align_size = 64;
    } else if (__builtin_cpu_supports("avx512f")) {
        impl->axpy = oblas16_axpy_avx512;
        impl->scal = oblas16_scal_avx512;
        impl->axiy = oblas16_axiy_avx512;
        impl->align_size = 64;
    } else
#endif
#if !defined(_MSC_VER) || defined(__AVX2__)
        if (__builtin_cpu_supports("avx2") && __builtin_cpu_supports("gfni")) {
        impl->axpy = oblas16_axpy_avx2_gfni;
        impl->scal = oblas16_scal_avx2_gfni;
        impl->axiy = oblas16_axiy_avx2_gfni;
        impl->align_size = 32;
    } else if (__builtin_cpu_supports("avx2")) {
        impl->axpy = oblas16_axpy_avx2;
        impl->scal = oblas16_scal_avx2;
        impl->axiy = oblas16_axiy_avx2;
        impl->align_size = 32;
    } else
#endif
        if (__builtin_cpu_supports("ssse3") && __builtin_cpu_supports("gfni")) {
        impl->axpy = oblas16_axpy_ssse3_gfni;
        impl->scal = oblas16_scal_ssse3_gfni;
        impl->axiy = oblas16_axiy_ssse3_gfni;
        impl->align_size = 16;
    } else if (__builtin_cpu_supports("ssse3")) {
        impl->axpy = oblas16_axpy_ssse3;
        impl->scal = oblas16_scal_ssse3;
        impl->axiy = oblas16_axiy_ssse3;
        impl->align_size = 16;
    }
#elif defined(OBLAS_ARCH_ARM)
    impl->axpy = oblas16_axpy_neon;
    impl->scal = oblas16_scal_neon;
    impl->axiy = oblas16_axiy_neon;
    impl->align_size = 16;
#elif defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector)
    impl->axpy = oblas16_axpy_rvv;
    impl->scal = oblas16_scal_rvv;
    impl->axiy = oblas16_axiy_rvv;
    impl->align_size = 16;
#endif
}

void oblas16_get_impl_by_name(struct oblas16_impl *impl, const char *name)
{
    impl->axpy = oblas16_axpy_ref;
    impl->scal = oblas16_scal_ref;
    impl->axiy = oblas16_axiy_ref;
    impl->align_size = sizeof(void *);

#if defined(OBLAS_ARCH_X86)
#if !defined(_MSC_VER) || defined(__AVX512F__)
    if (strcmp(name, "avx512_gfni") == 0) {
        impl->axpy = oblas16_axpy_avx512_gfni;
        impl->scal = oblas16_scal_avx512_gfni;
        impl->axiy = oblas16_axiy_avx512_gfni;
        impl->align_size = 64;
    } else if (strcmp(name, "avx512") == 0) {
        impl->axpy = oblas16_axpy_avx512;
        impl->scal = oblas16_scal_avx512;
        impl->axiy = oblas16_axiy_avx512;
        impl->align_size = 64;
    } else
#endif
#if !defined(_MSC_VER) || defined(__AVX2__)
        if (strcmp(name, "avx2_gfni") == 0) {
        impl->axpy = oblas16_axpy_avx2_gfni;
        impl->scal = oblas16_scal_avx2_gfni;
        impl->axiy = oblas16_axiy_avx2_gfni;
        impl->align_size = 32;
    } else if (strcmp(name, "avx2") == 0) {
        impl->axpy = oblas16_axpy_avx2;
        impl->scal = oblas16_scal_avx2;
        impl->axiy = oblas16_axiy_avx2;
        impl->align_size = 32;
    } else
#endif
        if (strcmp(name, "ssse3_gfni") == 0) {
        impl->axpy = oblas16_axpy_ssse3_gfni;
        impl->scal = oblas16_scal_ssse3_gfni;
        impl->axiy = oblas16_axiy_ssse3_gfni;
        impl->align_size = 16;
    } else if (strcmp(name, "ssse3") == 0) {
        impl->axpy = oblas16_axpy_ssse3;
        impl->scal = oblas16_scal_ssse3;
        impl->axiy = oblas16_axiy_ssse3;
        impl->align_size = 16;
    }
#elif defined(OBLAS_ARCH_ARM)
    if (strcmp(name, "neon") == 0) {
        impl->axpy = oblas16_axpy_neon;
        impl->scal = oblas16_scal_neon;
        impl->axiy = oblas16_axiy_neon;
        impl->align_size = 16;
    }
#elif defined(OBLAS_ARCH_RISCV) && defined(__riscv_vector)
    if (strcmp(name, "rvv") == 0) {
        impl->axpy = oblas16_axpy_rvv;
        impl->scal = oblas16_scal_rvv;
        impl->axiy = oblas16_axiy_rvv;
        impl->align_size = 16;
    }
#endif
}
