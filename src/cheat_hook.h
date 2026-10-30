/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef SPARTAN_CHEAT_HOOK_H
#define SPARTAN_CHEAT_HOOK_H
#include <stdint.h>
#include <string.h>
#include "signature.h"

/* Basara 4 cheats ported by i7Exceed (Sumeragi Native+).
 * Cheat codes and offsets by Q8Despair / GoldHEN Cheat Repository (CUSA01159 v01.02).
 * Reference: https://github.com/GoldHEN/GoldHEN_Cheat_Repository/blob/main/json/CUSA01159_01.02.json */

enum {
    BASARA4_CHEAT_GOLD,
    BASARA4_CHEAT_COINS,
    BASARA4_CHEAT_GROWTH_POINTS,
    BASARA4_CHEAT_WEAPON_POINTS,
    BASARA4_CHEAT_WEAPON_RANK,
    BASARA4_CHEAT_COUNT
};

#define SUMERAGI_CHEAT_GOLD BASARA4_CHEAT_GOLD
#define SUMERAGI_CHEAT_COINS BASARA4_CHEAT_COINS
#define SUMERAGI_CHEAT_GROWTH_POINTS BASARA4_CHEAT_GROWTH_POINTS
#define SUMERAGI_CHEAT_WEAPON_POINTS BASARA4_CHEAT_WEAPON_POINTS
#define SUMERAGI_CHEAT_WEAPON_RANK BASARA4_CHEAT_WEAPON_RANK
#define SUMERAGI_CHEAT_COUNT BASARA4_CHEAT_COUNT

#define SPARTAN_CHEAT_WEAPON_RANK BASARA4_CHEAT_WEAPON_RANK
#define SPARTAN_CHEAT_COUNT BASARA4_CHEAT_COUNT

typedef struct {
    uint32_t offset;
    unsigned id, length, replacement_length;
    unsigned char original[9], replacement[40];
    SpartanSignature site;  /* finds the hook in any build; offset is the reference build's */
} SpartanCheatHook;

static const SpartanCheatHook spartanport_cheat_hooks[] = {
    /* 1. Gold Won't Decrease: original at 0x529a91 (fo 0x52da91) mov [rax+0xdc], ecx (6 bytes).
     * Replacement: maxes wallet [rax+0xdc] = 0x09999999 and sets eax = 0x09999999 (15 bytes). */
    {0x529a91, BASARA4_CHEAT_GOLD, 6, 15,
     {0x89, 0x88, 0xdc, 0, 0, 0},
     {0xc7, 0x80, 0xdc, 0, 0, 0, 0x99, 0x99, 0x99, 0x09, 0xb8, 0x99, 0x99, 0x99, 0x09},
     {"41 2b 8e 70 07 00 00 41 0f 48 cf 89 88 dc 00 00 00", 11}},

    /* 2. Special Coin x99999: original at 0x582b0 (fo 0x5c2b0) lock add [rdi+0x2e8], esi (7 bytes).
     * Replacement: mov dword ptr [rdi+0x2e8], 999999 (0x000F423F) (10 bytes). */
    {0x582b0, BASARA4_CHEAT_COINS, 7, 10,
     {0xf0, 0x01, 0xb7, 0xe8, 0x02, 0, 0},
     {0xc7, 0x87, 0xe8, 0x02, 0, 0, 0x3f, 0x42, 0x0f, 0},
     {"f0 01 b7 e8 02 00 00 81 bf e8 02 00 00 40 42 0f 00", 0}},

    /* 3a. Growth points won't decrease (Batch): original at 0x5028d4 sub [r13+0x6b4], eax (7 bytes).
     * Replacement: maxes [r13+0x6b4] = 0x09999999 and sets eax = 0x09999999 (16 bytes). */
    {0x5028d4, BASARA4_CHEAT_GROWTH_POINTS, 7, 16,
     {0x41, 0x29, 0x85, 0xb4, 0x06, 0, 0},
     {0x41, 0xc7, 0x85, 0xb4, 0x06, 0, 0, 0x99, 0x99, 0x99, 0x09, 0xb8, 0x99, 0x99, 0x99, 0x09},
     {"49 8b 8d 20 06 00 00 41 8b 85 b8 06 00 00 41 29 85 b4 06 00 00", 14}},

    /* 3b. Growth points won't decrease (Single): original at 0x5026d8 sub [r13+0x6b4], eax (7 bytes).
     * Replacement: maxes [r13+0x6b4] = 0x09999999 and sets eax = 0x09999999 (16 bytes). */
    {0x5026d8, BASARA4_CHEAT_GROWTH_POINTS, 7, 16,
     {0x41, 0x29, 0x85, 0xb4, 0x06, 0, 0},
     {0x41, 0xc7, 0x85, 0xb4, 0x06, 0, 0, 0x99, 0x99, 0x99, 0x09, 0xb8, 0x99, 0x99, 0x99, 0x09},
     {"85 68 06 00 00 00 00 00 00 41 8b 85 b8 06 00 00 41 29 85 b4 06 00 00", 16}},

    /* 4. Weapon points won't decrease: original at 0x560fc6 mov [rcx+0xe4], eax (6 bytes).
     * Replacement: maxes [rcx+0xe4] = 0x00099999 and sets eax = 0x00099999 (15 bytes). */
    {0x560fc6, BASARA4_CHEAT_WEAPON_POINTS, 6, 15,
     {0x89, 0x81, 0xe4, 0, 0, 0},
     {0xc7, 0x81, 0xe4, 0, 0, 0, 0x99, 0x99, 0x09, 0, 0xb8, 0x99, 0x99, 0x09, 0},
     {"41 89 96 38 06 00 00 49 8b 0f 89 81 e4 00 00 00 41 8b b6 20 06 00 00", 10}},

    /* 5. Weapon rank points won't decrease: original at 0x55e6a1 mov [rax+0xe8], edx (6 bytes).
     * Replacement: maxes [rax+0xe8] = 0x00000099 and sets eax = 0x00000099 (15 bytes). */
    {0x55e6a1, BASARA4_CHEAT_WEAPON_RANK, 6, 15,
     {0x89, 0x90, 0xe8, 0, 0, 0},
     {0xc7, 0x80, 0xe8, 0, 0, 0, 0x99, 0, 0, 0, 0xb8, 0x99, 0, 0, 0},
     {"83 f9 02 41 0f 4c d7 89 90 e8 00 00 00 c4 c1 7a 10 8e c4 07 00 00", 7}}
};
#define SPARTAN_CHEAT_HOOK_COUNT (sizeof(spartanport_cheat_hooks)/sizeof(spartanport_cheat_hooks[0]))
static inline int spartanport_cheat_signature_match(const SpartanCheatHook *hook,
                                            const unsigned char *code, uint64_t size) {
    return size >= hook->length && !memcmp(code, hook->original, hook->length);
}
static inline unsigned spartanport_cheat_build_stub(unsigned char *out, const SpartanCheatHook *hook,
                                            uintptr_t flag, uintptr_t resume) {
    /* Keep the SysV red zone, GPRs and condition flags intact; no host call on the hot path. */
    const unsigned char prefix[] = {
        0x48,0x8d,0x64,0x24,0x80,0x9c,0x50,0x48,0xb8,0,0,0,0,0,0,0,0,
        0x80,0x38,0,0x74,0
    };
    const unsigned char restore[] = {0x58,0x9d};
    const unsigned char stack[] = {0x48,0x8d,0xa4,0x24,0x80,0,0,0};
    const unsigned char jump[] = {0xff,0x25,0,0,0,0};
    unsigned n = sizeof(prefix);
    memcpy(out, prefix, n);
    memcpy(out + 9, &flag, 8);
    out[21] = (unsigned char)(sizeof(restore) + hook->replacement_length + sizeof(stack) + 14);
    memcpy(out+n,restore,sizeof(restore)); n += sizeof(restore);
    memcpy(out+n,hook->replacement,hook->replacement_length); n += hook->replacement_length;
    memcpy(out+n,stack,sizeof(stack)); n += sizeof(stack);
    memcpy(out+n,jump,sizeof(jump)); n += sizeof(jump);
    memcpy(out+n,&resume,8); n += 8;
    memcpy(out+n,restore,sizeof(restore)); n += sizeof(restore);
    memcpy(out+n,hook->original,hook->length); n += hook->length;
    memcpy(out+n,stack,sizeof(stack)); n += sizeof(stack);
    memcpy(out+n,jump,sizeof(jump)); n += sizeof(jump);
    memcpy(out+n,&resume,8); n += 8;
    return n;
}
#endif
