/* Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (LICENSE, NOTICE.md). */
/* Interactive SDL3 gamepad mapper for controllers SDL does not know (generic DirectInput pads).
 * Asks for each PlayStation button/stick in turn, records the joystick input that moved, and
 * writes one SDL mapping line (GUID,name,a:b2,...) to the file given as argv[1]. The runtime
 * loads it through SDL_GAMECONTROLLERCONFIG_FILE. Wait 15 s without input to skip an entry. */
#include <SDL3/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { const char *sdl, *prompt; int axis; } Target;
/* Prompts use the Xbox names printed on the controller; SDL's a/b/x/y are positions (south, east,
 * west, north), which the game reads as Cross, Circle, Square and Triangle. */
static const Target targets[] = {
    {"a", "A (bawah)  -> Cross di game", 0},       {"b", "B (kanan)  -> Circle di game", 0},
    {"x", "X (kiri)   -> Square di game", 0},      {"y", "Y (atas)   -> Triangle di game", 0},
    {"leftshoulder", "LB  -> L1", 0},              {"rightshoulder", "RB  -> R1", 0},
    {"lefttrigger", "LT  -> L2", 0},               {"righttrigger", "RT  -> R2", 0},
    {"back", "BACK / VIEW  -> touchpad (menu pause)", 0},
    {"start", "START / MENU  -> Options", 0},
    {"leftstick", "LS (tekan stick kiri)  -> L3", 0},
    {"rightstick", "RS (tekan stick kanan)  -> R3", 0},
    {"guide", "GUIDE / HOME (diam 15 detik bila tidak ada)", 0},
    {"dpup", "D-PAD ATAS", 0},                     {"dpdown", "D-PAD BAWAH", 0},
    {"dpleft", "D-PAD KIRI", 0},                   {"dpright", "D-PAD KANAN", 0},
    {"leftx", "STICK KIRI ke KANAN", 1},           {"lefty", "STICK KIRI ke BAWAH", 1},
    {"rightx", "STICK KANAN ke KANAN", 1},         {"righty", "STICK KANAN ke BAWAH", 1},
};
enum { MAX_AXES = 16 };
static Sint16 rest[MAX_AXES];
static SDL_Joystick *joy;
static int axis_count;
static int joy_axis(int a) { return SDL_GetJoystickAxis(joy, a); }

/* Waits until no button is held, hats are centered and axes are back near rest. */
static void wait_release(SDL_Joystick *j) {
    for (int stable = 0; stable < 15;) {
        SDL_Delay(20);
        SDL_UpdateJoysticks();
        int idle = 1;
        for (int b = 0; b < SDL_GetNumJoystickButtons(j); ++b) idle &= !SDL_GetJoystickButton(j, b);
        for (int h = 0; h < SDL_GetNumJoystickHats(j); ++h) idle &= SDL_GetJoystickHat(j, h) == SDL_HAT_CENTERED;
        for (int a = 0; a < SDL_GetNumJoystickAxes(j) && a < MAX_AXES; ++a)
            idle &= abs(SDL_GetJoystickAxis(j, a) - rest[a]) < 12000;
        stable = idle ? stable + 1 : 0;
    }
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}
}

/* Bindings already given to an earlier entry: a second entry never takes one of them. */
static char used[64][16];
static int used_count;
static int already_used(const char *binding) {
    for (int i = 0; i < used_count; ++i) if (!strcmp(used[i], binding)) return 1;
    return 0;
}

/* Returns 1 and the SDL binding ("b3", "h0.1", "a2", "+a4", "-a1") of the first new input that moves. */
static int capture(int want_axis, char *out, size_t size) {
    Uint64 deadline = SDL_GetTicks() + 15000;
    while (SDL_GetTicks() < deadline) {
        SDL_Event e;
        if (!SDL_WaitEventTimeout(&e, 50)) continue;
        char candidate[16] = "";
        if (!want_axis && e.type == SDL_EVENT_JOYSTICK_BUTTON_DOWN) {
            snprintf(candidate, sizeof candidate, "b%d", e.jbutton.button);
        } else if (!want_axis && e.type == SDL_EVENT_JOYSTICK_HAT_MOTION && e.jhat.value != SDL_HAT_CENTERED) {
            snprintf(candidate, sizeof candidate, "h%d.%d", e.jhat.hat, e.jhat.value);
        } else if (e.type == SDL_EVENT_JOYSTICK_AXIS_MOTION && e.jaxis.axis < MAX_AXES) {
            int delta = e.jaxis.value - rest[e.jaxis.axis];
            if (abs(delta) < 20000) continue;
            if (want_axis) {
                /* Sticks move two axes at once and jitter: watch every axis for 700 ms after the
                 * first big movement and take the unused one that moved furthest. */
                int best = -1, best_delta = 0;
                Uint64 until = SDL_GetTicks() + 700;
                while (SDL_GetTicks() < until) {
                    SDL_Delay(10);
                    SDL_UpdateJoysticks();
                    for (int a = 0; a < MAX_AXES && a < axis_count; ++a) {
                        char name[8]; snprintf(name, sizeof name, "a%d", a);
                        if (already_used(name)) continue;
                        int d = joy_axis(a) - rest[a];
                        if (abs(d) > abs(best_delta)) { best = a; best_delta = d; }
                    }
                }
                if (best < 0 || abs(best_delta) < 20000) continue;
                snprintf(candidate, sizeof candidate, best_delta > 0 ? "a%d" : "a%d~", best);
                SDL_Event drop; while (SDL_PollEvent(&drop)) {}
            } else if (abs(rest[e.jaxis.axis]) > 20000) {
                /* A trigger axis resting at one end: the full range is the trigger. */
                snprintf(candidate, sizeof candidate, "a%d", e.jaxis.axis);
            } else {
                snprintf(candidate, sizeof candidate, delta > 0 ? "+a%d" : "-a%d", e.jaxis.axis);
            }
        }
        if (!candidate[0]) continue;
        /* "a1" and "a1~" are the same stick axis. */
        char base[16]; snprintf(base, sizeof base, "%s", candidate);
        char *tilde = strchr(base, '~'); if (tilde) *tilde = 0;
        if (already_used(base)) { printf("[%s sudah dipakai] ", base); fflush(stdout); continue; }
        if (used_count < 64) snprintf(used[used_count++], sizeof used[0], "%s", base);
        snprintf(out, size, "%s", candidate);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "gamepad_mapping.txt";
    /* The console window is not an SDL window: without this SDL drops joystick events as background input. */
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (!SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMEPAD)) { printf("SDL_Init gagal: %s\n", SDL_GetError()); return 1; }
    SDL_Delay(500);
    int count = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&count);
    if (!ids || count < 1) { printf("Tidak ada controller terdeteksi. Colokkan lalu jalankan lagi.\n"); return 1; }
    SDL_Joystick *j = SDL_OpenJoystick(ids[0]);
    if (!j) { printf("Tidak bisa membuka controller: %s\n", SDL_GetError()); return 1; }
    joy = j;
    axis_count = SDL_GetNumJoystickAxes(j);
    char guid[64];
    SDL_GUIDToString(SDL_GetJoystickGUID(j), guid, sizeof guid);
    const char *name = SDL_GetJoystickName(j);
    printf("Controller: %s (GUID %s), %d tombol, %d axis, %d hat\n", name ? name : "?", guid,
           SDL_GetNumJoystickButtons(j), SDL_GetNumJoystickAxes(j), SDL_GetNumJoystickHats(j));
    printf("Lepaskan semua tombol dan stick. Tiap langkah: tekan sekali lalu lepas; diam 15 detik = lewati.\n");
    SDL_Delay(800);
    SDL_UpdateJoysticks();
    for (int a = 0; a < SDL_GetNumJoystickAxes(j) && a < MAX_AXES; ++a) rest[a] = SDL_GetJoystickAxis(j, a);
    SDL_Event e;
    while (SDL_PollEvent(&e)) {}

    char mapping[2048];
    snprintf(mapping, sizeof mapping, "%s,%s,", guid, name && *name ? name : "Gamepad");
    /* --sticks: keep the buttons of an existing mapping for this controller, record only the sticks. */
    int sticks_only = argc > 2 && !strcmp(argv[2], "--sticks");
    if (sticks_only) {
        char line[2048] = "";
        FILE *old = fopen(path, "r");
        if (!old || !fgets(line, sizeof line, old) || strncmp(line, guid, strlen(guid))) {
            printf("Tidak ada mapping lama untuk controller ini di %s; jalankan tanpa --sticks.\n", path);
            if (old) fclose(old);
            return 1;
        }
        fclose(old);
        line[strcspn(line, "\r\n")] = 0;
        char *field = strtok(line, ","); /* GUID */
        field = strtok(NULL, ",");       /* name */
        while ((field = strtok(NULL, ","))) {
            const char *colon = strchr(field, ':');
            if (!colon || !strncmp(field, "leftx:", 6) || !strncmp(field, "lefty:", 6) ||
                !strncmp(field, "rightx:", 7) || !strncmp(field, "righty:", 7) || !strncmp(field, "platform:", 9))
                continue;
            size_t at = strlen(mapping);
            snprintf(mapping + at, sizeof mapping - at, "%s,", field);
            if (used_count < 64) snprintf(used[used_count++], sizeof used[0], "%s", colon + 1);
        }
        printf("Tombol lama dipakai lagi; sekarang hanya stick.\n");
    }
    for (size_t i = 0; i < sizeof targets / sizeof *targets; ++i) {
        if (sticks_only && !targets[i].axis) continue;
        printf("Tekan %s ... ", targets[i].prompt);
        fflush(stdout);
        char binding[16];
        if (capture(targets[i].axis, binding, sizeof binding)) {
            printf("%s\n", binding);
            size_t used = strlen(mapping);
            snprintf(mapping + used, sizeof mapping - used, "%s:%s,", targets[i].sdl, binding);
        } else {
            printf("dilewati\n");
        }
        wait_release(j);
    }
    size_t used = strlen(mapping);
    snprintf(mapping + used, sizeof mapping - used, "platform:Windows,");
    FILE *f = fopen(path, "w");
    if (!f) { printf("Tidak bisa menulis %s\n", path); return 1; }
    fprintf(f, "%s\n", mapping);
    fclose(f);
    printf("\nMapping disimpan ke %s:\n%s\n", path, mapping);
    SDL_CloseJoystick(j);
    SDL_free(ids);
    SDL_Quit();
    return 0;
}
