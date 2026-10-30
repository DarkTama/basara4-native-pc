/* Unit tests for DLC discovery, enumeration, mounting, file I/O, and unmounting. */
#define _GNU_SOURCE
#include "../src/runtime_content.c"
#include "../src/runtime_file.c"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int32_t guest_errno;
int32_t *runtime_errno(void) { return &guest_errno; }
int32_t runtime_guest_errno(int e) { return e; }
uintptr_t runtime_lookup(const RuntimeExport *table, size_t n, const char *name) {
    for (size_t i = 0; i < n; ++i) if (!strcmp(table[i].name, name)) return (uintptr_t)table[i].function;
    return 0;
}
int runtime_audio_log_enabled(void) { return 0; }
void runtime_audio_log(const char *fmt, ...) { (void)fmt; }
#ifdef _WIN32
int compat_errno_from_win32(unsigned long error) { (void)error; return 5; }
int64_t compat_pread(int fd, void *buf, uint64_t count, int64_t offset) { (void)fd; (void)buf; (void)count; (void)offset; return -1; }
int64_t compat_pwrite(int fd, const void *buf, uint64_t count, int64_t offset) { (void)fd; (void)buf; (void)count; (void)offset; return -1; }
#endif

#define ORBIS_OK 0
#define APP_CONTENT_PARAMETER ((int32_t)0x80D90002)
#define APP_CONTENT_NOT_FOUND ((int32_t)0x80D90005)

typedef int32_t (ABI *Init)(const void *, void *);
typedef int32_t (ABI *List)(uint32_t, void *, uint32_t, uint32_t *);

#pragma pack(push, 1)
typedef struct {
    char magic[8];        /* B4ADD001 */
    uint32_t version;     /* 1 */
    uint32_t count;       /* N */
    char title_id[16];    /* CUSA01159 */
} ManifestHeader;

typedef struct {
    uint32_t service_label;
    char entitlement_label[20];
    uint32_t status;
    char host_path[512];
    uint32_t flags;
} ManifestEntry;
#pragma pack(pop)

static void write_test_manifest(const char *path, const char *magic, uint32_t version,
                                const char *title_id, uint32_t count, const ManifestEntry *entries) {
    FILE *f = fopen(path, "wb");
    assert(f != NULL);
    ManifestHeader hdr;
    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, magic, 8);
    hdr.version = version;
    hdr.count = count;
    strncpy(hdr.title_id, title_id, 15);
    assert(fwrite(&hdr, 1, sizeof(hdr), f) == sizeof(hdr));
    for (uint32_t i = 0; i < count; ++i) {
        assert(fwrite(&entries[i], 1, sizeof(ManifestEntry), f) == sizeof(ManifestEntry));
    }
    fclose(f);
}

int main(void) {
    printf("--- Running DLC C Runtime Unit Test ---\n");

    /* Create temporary directories */
    char tmpdir[] = "out/test_dlc_rt_XXXXXX";
#ifdef _WIN32
    static int seed = 5678;
    snprintf(tmpdir, sizeof(tmpdir), "out/test_dlc_rt_%u", (unsigned)++seed);
    mkdir("out", 0755);
    mkdir(tmpdir, 0755);
#else
    assert(mkdtemp(tmpdir));
#endif

    char dlc1_path[512], dlc2_path[512];
    snprintf(dlc1_path, sizeof(dlc1_path), "%s/dlc1", tmpdir);
    snprintf(dlc2_path, sizeof(dlc2_path), "%s/dlc2", tmpdir);
    mkdir(dlc1_path, 0755);
    mkdir(dlc2_path, 0755);

    /* Write test content files */
    char file1[512], file2[512];
    snprintf(file1, sizeof(file1), "%s/fixture1.bin", dlc1_path);
    snprintf(file2, sizeof(file2), "%s/fixture2.bin", dlc2_path);
    FILE *f1 = fopen(file1, "wb"); assert(f1); fputs("DLC1_CONTENT_DATA", f1); fclose(f1);
    FILE *f2 = fopen(file2, "wb"); assert(f2); fputs("DLC2_EXTRA_SWORD", f2); fclose(f2);

    /* 1. Test invalid manifests */
    char bad_manifest[512];
    snprintf(bad_manifest, sizeof(bad_manifest), "%s/bad_magic.bin", tmpdir);
    write_test_manifest(bad_manifest, "BADMAGIC", 1, "CUSA01159", 0, NULL);
    assert(runtime_content_configure_addons(bad_manifest, "CUSA01159") == -1);
    assert(strstr(runtime_content_addon_reject_reason(), "magic") != NULL);

    char bad_ver_manifest[512];
    snprintf(bad_ver_manifest, sizeof(bad_ver_manifest), "%s/bad_ver.bin", tmpdir);
    write_test_manifest(bad_ver_manifest, "B4ADD001", 99, "CUSA01159", 0, NULL);
    assert(runtime_content_configure_addons(bad_ver_manifest, "CUSA01159") == -1);
    assert(strstr(runtime_content_addon_reject_reason(), "version") != NULL);

    char bad_title_manifest[512];
    snprintf(bad_title_manifest, sizeof(bad_title_manifest), "%s/bad_title.bin", tmpdir);
    write_test_manifest(bad_title_manifest, "B4ADD001", 1, "CUSA99999", 0, NULL);
    assert(runtime_content_configure_addons(bad_title_manifest, "CUSA01159") == -1);
    assert(strstr(runtime_content_addon_reject_reason(), "mismatch") != NULL);

    /* 2. Test valid manifest with 2 DLCs */
    static ManifestEntry entries[2];
    memset(entries, 0, sizeof(entries));
    entries[0].service_label = 0;
    strncpy(entries[0].entitlement_label, "COSTUME000000001", 19);
    entries[0].status = 4;
    strncpy(entries[0].host_path, dlc1_path, 511);

    entries[1].service_label = 0;
    strncpy(entries[1].entitlement_label, "WEAPON0000000001", 19);
    entries[1].status = 4;
    strncpy(entries[1].host_path, dlc2_path, 511);

    char good_manifest[512];
    snprintf(good_manifest, sizeof(good_manifest), "%s/good_manifest.bin", tmpdir);
    write_test_manifest(good_manifest, "B4ADD001", 1, "CUSA01159", 2, entries);

    assert(runtime_content_configure_addons(good_manifest, "CUSA01159") == 0);

    /* Setup filesystem runtime */
    char app0[512], user[512];
    snprintf(app0, sizeof(app0), "%s/app0", tmpdir);
    snprintf(user, sizeof(user), "%s/user", tmpdir);
    mkdir(app0, 0755);
    mkdir(user, 0755);
    runtime_file_configure(app0, user);

    /* Setup content runtime */
    const uint32_t profile[] = {3, 0, 0, 0, 0};
    runtime_content_configure(profile);

    assert(module_load(0xb4) == 0);

    Init init = (Init)runtime_content_resolve("R9lA82OraNs#libSceAppContent");
    List list = (List)runtime_content_resolve("xnd8BJzAxmk#libSceAppContent");
    assert(init && list);

    unsigned char initial[32] = {0};
    unsigned char boot[40] = {0};
    assert(init(initial, boot) == 0);

    /* Test List: query matching count with capacity = 0 */
    uint32_t hits = 0;
    assert(list(0, NULL, 0, &hits) == 0);
    assert(hits == 2);

    /* Test List: query with capacity = 1 (partial) */
    unsigned char list_buf[48] = {0};
    assert(list(0, list_buf, 1, &hits) == 0);
    assert(hits == 1);
    assert(!strcmp((char *)list_buf, "COSTUME000000001"));
    uint32_t status_val = 0;
    memcpy(&status_val, list_buf + 20, 4);
    assert(status_val == 4);

    /* Test List: query with capacity = 2 (all) */
    memset(list_buf, 0, sizeof(list_buf));
    assert(list(0, list_buf, 2, &hits) == 0);
    assert(hits == 2);
    assert(!strcmp((char *)list_buf, "COSTUME000000001"));
    assert(!strcmp((char *)(list_buf + 24), "WEAPON0000000001"));

    /* Test List: invalid service label > 7 */
    assert((uint32_t)list(8, list_buf, 2, &hits) == (uint32_t)APP_CONTENT_PARAMETER);

    /* Test Mount: invalid parameters */
    char mp[16] = {0};
    assert((uint32_t)runtime_content_addcont_mount(9, "COSTUME000000001", mp) == (uint32_t)APP_CONTENT_PARAMETER);
    assert((uint32_t)runtime_content_addcont_mount(0, NULL, mp) == (uint32_t)APP_CONTENT_PARAMETER);
    assert((uint32_t)runtime_content_addcont_mount(0, "COSTUME000000001", NULL) == (uint32_t)APP_CONTENT_PARAMETER);

    /* Test Mount: unknown entitlement */
    assert((uint32_t)runtime_content_addcont_mount(0, "UNKNOWN_ENTITL01", mp) == (uint32_t)APP_CONTENT_NOT_FOUND);

    /* Test Mount: valid entitlement 1 */
    assert(runtime_content_addcont_mount(0, "COSTUME000000001", mp) == 0);
    assert(!strcmp(mp, "/addcont0"));

    /* Test Guest File I/O: open and read from /addcont0/fixture1.bin */
    int64_t fd = do_open("/addcont0/fixture1.bin", 0, 0);
    assert(fd >= 3);
    char read_buf[32] = {0};
    int64_t nread = do_read((int)fd, read_buf, sizeof(read_buf));
    assert(nread > 0);
    assert(!strcmp(read_buf, "DLC1_CONTENT_DATA"));
    assert(do_close((int)fd) == 0);

    /* Test Mount: valid entitlement 2 */
    char mp2[16] = {0};
    assert(runtime_content_addcont_mount(0, "WEAPON0000000001", mp2) == 0);
    assert(!strcmp(mp2, "/addcont1"));

    /* Test Guest File I/O: open and read from /addcont1/fixture2.bin */
    fd = do_open("/addcont1/fixture2.bin", 0, 0);
    assert(fd >= 3);
    memset(read_buf, 0, sizeof(read_buf));
    nread = do_read((int)fd, read_buf, sizeof(read_buf));
    assert(nread > 0);
    assert(!strcmp(read_buf, "DLC2_EXTRA_SWORD"));
    assert(do_close((int)fd) == 0);

    /* Test Mount: remounting same entitlement returns existing slot */
    char mp_dup[16] = {0};
    assert(runtime_content_addcont_mount(0, "COSTUME000000001", mp_dup) == 0);
    assert(!strcmp(mp_dup, "/addcont0"));

    /* Test Unmount: unmount /addcont0 */
    assert(runtime_content_addcont_unmount("/addcont0") == 0);
    /* Now /addcont0/fixture1.bin should fail to open */
    fd = do_open("/addcont0/fixture1.bin", 0, 0);
    assert(fd < 0);

    /* But /addcont1/fixture2.bin must still be accessible! */
    fd = do_open("/addcont1/fixture2.bin", 0, 0);
    assert(fd >= 3);
    assert(do_close((int)fd) == 0);

    /* Unmount /addcont1 */
    assert(runtime_content_addcont_unmount("/addcont1") == 0);
    fd = do_open("/addcont1/fixture2.bin", 0, 0);
    assert(fd < 0);

    /* Clean up temporary test files */
#ifdef _WIN32
    system("rmdir /s /q out\\test_dlc_rt_5679 2>nul");
#else
    system("rm -rf out/test_dlc_rt_5679");
#endif

    puts("PASS: DLC Runtime discovery, enumeration, mounting, I/O and unmounting");
    return 0;
}
