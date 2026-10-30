/* Offline AppContent provider for the configured base-game profile and local add-on DLCs.
 * Based on Basara 4 Native PC by Razz (2026), under GNU GPL v2 or later.
 * Modified by i7Exceed (October 2026) for Sumeragi Native+: local DLC manifest parsing,
 * AppContent enumeration, and guest addcont mount binding.
 */
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define INVALID_ID ((int32_t)0x805a1000)
#define NOT_LOADED ((int32_t)0x805a1001)
#define PARAMETER ((int32_t)0x80d90002)
#define BUSY ((int32_t)0x80d90003)
static unsigned refs, initialized, configured;
static uint32_t parameters[5];
static unsigned queries, lists;
void runtime_content_configure(const uint32_t values[5]) {
    if (refs || initialized || (values[0]!=1 && values[0]!=3)) {
        fputs("ERROR: invalid or late content profile\n",stderr); exit(1);
    }
    memcpy(parameters,values,sizeof(parameters)); configured=1;
}
static void require_provider(void) {
    if (!configured) { fputs("STOP: AppContent needs --content-profile\n",stderr); exit(21); }
}
/* System modules: every library this eboot imports is provided by the host
 * runtime, so loading one only tracks a reference count. AppContent (0xb4)
 * additionally requires the configured content profile. */
static unsigned module_refs[0x10000];
static ABI int32_t module_load(uint16_t id) {
    if (!id) return INVALID_ID;
    if (id==0xb4) require_provider();
    if (module_refs[id]==UINT32_MAX) { fputs("STOP: module reference overflow\n",stderr); exit(21); }
    ++module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; printf("Runtime: AppContent host provider loaded; references=%u\n",refs); }
    else printf("Runtime: system module 0x%x loaded (host implementation)\n",id);
    return 0;
}
static ABI int32_t module_loaded(uint16_t id) {
    if (!id) return INVALID_ID;
    return module_refs[id] ? 0 : NOT_LOADED;
}
static ABI int32_t module_unload(uint16_t id) {
    int32_t result=module_loaded(id);
    if (result) return result;
    --module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; if (!refs) initialized=0; }
    return 0;
}
static void require_loaded(void) {
    require_provider();
    if (!refs) { fputs("STOP: AppContent called before module load\n",stderr); exit(21); }
}
#define MAX_ADDONS 64
#define MAX_MOUNT_SLOTS 16

typedef struct {
    uint32_t service_label;
    char entitlement_label[20];
    uint32_t status;
    char host_path[512];
} RuntimeAddon;

typedef struct {
    int active;
    char entitlement_label[20];
    char guest_path[16];
} ActiveMountSlot;

static RuntimeAddon addons[MAX_ADDONS];
static uint32_t addon_count = 0;
static ActiveMountSlot active_mounts[MAX_MOUNT_SLOTS];
static unsigned mounts_count = 0;

int runtime_content_configure_addons(const char *path, const char *expected_title_id) {
    if (!path) return -1;
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "Runtime: cannot open add-on manifest %s\n", path);
        return -1;
    }
    unsigned char header[32];
    if (fread(header, 1, sizeof(header), f) != sizeof(header)) {
        fprintf(stderr, "Runtime: add-on manifest too short\n");
        fclose(f);
        return -1;
    }
    if (memcmp(header, "B4ADD001", 8) != 0) {
        fprintf(stderr, "Runtime: invalid add-on manifest magic\n");
        fclose(f);
        return -1;
    }
    uint32_t version, count;
    memcpy(&version, header + 8, 4);
    memcpy(&count, header + 12, 4);
    if (version != 1) {
        fprintf(stderr, "Runtime: unsupported add-on manifest version %u\n", version);
        fclose(f);
        return -1;
    }
    char manifest_title[16] = {0};
    memcpy(manifest_title, header + 16, 15);
    manifest_title[15] = 0;
    if (expected_title_id && expected_title_id[0] && strcmp(manifest_title, expected_title_id) != 0) {
        fprintf(stderr, "Runtime: add-on manifest title ID '%s' mismatch with game '%s'\n",
                manifest_title, expected_title_id);
        fclose(f);
        return -1;
    }
    if (count > MAX_ADDONS) {
        fprintf(stderr, "Runtime: add-on manifest has %u entries, max is %u\n", count, MAX_ADDONS);
        fclose(f);
        return -1;
    }
    addon_count = 0;
    for (uint32_t i = 0; i < count; ++i) {
        unsigned char entry[544];
        if (fread(entry, 1, sizeof(entry), f) != sizeof(entry)) {
            fprintf(stderr, "Runtime: truncated add-on manifest entry %u\n", i);
            fclose(f);
            return -1;
        }
        memcpy(&addons[i].service_label, entry, 4);
        memcpy(addons[i].entitlement_label, entry + 4, 20);
        addons[i].entitlement_label[19] = 0;
        memcpy(&addons[i].status, entry + 24, 4);
        memcpy(addons[i].host_path, entry + 28, 512);
        addons[i].host_path[511] = 0;
        addon_count++;
    }
    if (fgetc(f) != EOF) {
        fprintf(stderr, "Runtime: trailing data in add-on manifest\n");
        fclose(f);
        return -1;
    }
    fclose(f);
    printf("Runtime: loaded %u add-on(s) from %s (title=%s)\n", addon_count, path, manifest_title);
    return 0;
}

const char *runtime_content_get_addon_path(uint32_t service_label, const char *entitlement_label) {
    if (!entitlement_label) return NULL;
    for (uint32_t i = 0; i < addon_count; ++i) {
        if (addons[i].service_label == service_label &&
            !strncmp(addons[i].entitlement_label, entitlement_label, 16)) {
            return addons[i].host_path;
        }
    }
    return NULL;
}

int runtime_content_allocate_mount_slot(const char *entitlement_label, char *guest_path, size_t guest_size) {
    if (!entitlement_label || !guest_path || guest_size < 16) return -1;
    /* If already mounted, return existing slot */
    for (int i = 0; i < MAX_MOUNT_SLOTS; ++i) {
        if (active_mounts[i].active && !strncmp(active_mounts[i].entitlement_label, entitlement_label, 16)) {
            snprintf(guest_path, guest_size, "%s", active_mounts[i].guest_path);
            return i;
        }
    }
    /* Find first inactive slot */
    for (int i = 0; i < MAX_MOUNT_SLOTS; ++i) {
        if (!active_mounts[i].active) {
            active_mounts[i].active = 1;
            strncpy(active_mounts[i].entitlement_label, entitlement_label, 19);
            active_mounts[i].entitlement_label[19] = 0;
            snprintf(active_mounts[i].guest_path, sizeof(active_mounts[i].guest_path), "/addcont%d", i);
            snprintf(guest_path, guest_size, "%s", active_mounts[i].guest_path);
            mounts_count++;
            return i;
        }
    }
    return -1;
}

void runtime_content_release_mount_slot(int slot) {
    if (slot >= 0 && slot < MAX_MOUNT_SLOTS && active_mounts[slot].active) {
        active_mounts[slot].active = 0;
        if (mounts_count) mounts_count--;
    }
}

void runtime_content_addon_unmounted(const char *mount_point) {
    if (!mount_point) return;
    for (int i = 0; i < MAX_MOUNT_SLOTS; ++i) {
        if (active_mounts[i].active && !strcmp(active_mounts[i].guest_path, mount_point)) {
            active_mounts[i].active = 0;
            if (mounts_count) mounts_count--;
            break;
        }
    }
}

int32_t runtime_content_addcont_mount(uint32_t label, const void *entitlement, char *mount_point) {
    if (label > 7 || !entitlement || !mount_point) return PARAMETER;
    char ent[20] = {0};
    memcpy(ent, entitlement, 16);
    ent[16] = 0;
    const char *host_path = runtime_content_get_addon_path(label, ent);
    if (!host_path) {
        printf("Runtime: AppContent addcont_mount: '%s' (label %u) not found\n", ent, label);
        return ((int32_t)0x80D90005);
    }
    char guest_path[16] = {0};
    int slot = runtime_content_allocate_mount_slot(ent, guest_path, sizeof(guest_path));
    if (slot < 0) {
        fprintf(stderr, "Runtime: AppContent mount limit reached for add-on '%s'\n", ent);
        return PARAMETER;
    }
    if (runtime_file_mount(guest_path, host_path) != 0) {
        fprintf(stderr, "Runtime: AppContent failed to mount %s -> %s\n", guest_path, host_path);
        runtime_content_release_mount_slot(slot);
        return PARAMETER;
    }
    memcpy(mount_point, guest_path, strlen(guest_path) + 1);
    printf("Runtime: AppContent mount: %s -> %s (label=%u, entitlement=%s)\n",
           guest_path, host_path, label, ent);
    return 0;
}

int32_t runtime_content_addcont_unmount(const char *mount_point) {
    if (!mount_point) return PARAMETER;
    runtime_file_unmount(mount_point);
    runtime_content_addon_unmounted(mount_point);
    printf("Runtime: AppContent unmount: %s\n", mount_point);
    return 0;
}

static ABI int32_t content_init(const unsigned char *init,unsigned char *boot) {
    require_loaded();
    if (!init || !boot) return PARAMETER;
    if (initialized) return BUSY;
    /* Only the reserved-zero init profile is implemented. */
    for (unsigned i=0;i<32;++i) if (init[i]) return PARAMETER;
    memset(boot,0,40); initialized=1;
    if (addon_count) {
        printf("Runtime: AppContent initialized; offline profile with %u add-on(s) available\n", addon_count);
    } else {
        puts("Runtime: AppContent initialized; offline base-game profile, no mounted add-ons");
    }
    return 0;
}
static void require_initialized(void) {
    require_loaded();
    if (!initialized) { fputs("STOP: AppContent query before initialization\n",stderr); exit(21); }
}
static ABI int32_t param_int(uint32_t id,int32_t *out) {
    require_initialized();
    if (id>4 || !out) return PARAMETER;
    memcpy(out,&parameters[id],4); ++queries;
    printf("Runtime: AppContent parameter %u = %d\n",id,*out);
    return 0;
}
static ABI int32_t addon_list(uint32_t service,void *list,uint32_t capacity,uint32_t *hits) {
    require_initialized();
    if (service > 7) { fputs("STOP: invalid AppContent service label > 7\n",stderr); return PARAMETER; }
    uint32_t matching = 0;
    for (uint32_t i = 0; i < addon_count; ++i) {
        if (addons[i].service_label == service) matching++;
    }
    if ((!capacity || !list) && !hits) return PARAMETER;
    if (!capacity || !list) {
        if (hits) *hits = matching;
        ++lists;
        return 0;
    }
    uint32_t to_write = matching < capacity ? matching : capacity;
    uint32_t written = 0;
    for (uint32_t i = 0; i < addon_count && written < to_write; ++i) {
        if (addons[i].service_label == service) {
            unsigned char *slot = (unsigned char *)list + written * 24;
            memset(slot, 0, 24);
            memcpy(slot, addons[i].entitlement_label, 17);
            memcpy(slot + 20, &addons[i].status, 4);
            written++;
        }
    }
    if (hits) *hits = written;
    ++lists;
    return 0;
}
uintptr_t runtime_content_resolve(const char *name) {
    if (!strcmp(name,"g8cM39EUZ6o#libSceSysmodule")) return (uintptr_t)module_load;
    if (!strcmp(name,"fMP5NHUOaMk#libSceSysmodule")) return (uintptr_t)module_loaded;
    if (!strcmp(name,"eR2bZFAAU0Q#libSceSysmodule")) return (uintptr_t)module_unload;
    if (!strcmp(name,"R9lA82OraNs#libSceAppContent")) return (uintptr_t)content_init;
    if (!strcmp(name,"99b82IKXpH4#libSceAppContent")) return (uintptr_t)param_int;
    if (!strcmp(name,"xnd8BJzAxmk#libSceAppContent")) return (uintptr_t)addon_list;
    return 0;
}
void runtime_content_report(void) {
    printf("Runtime: AppContent references=%u, initialized=%u, parameter queries=%u, list queries=%u, addons=%u, active_mounts=%u\n",
           refs,initialized,queries,lists,addon_count,mounts_count);
}
