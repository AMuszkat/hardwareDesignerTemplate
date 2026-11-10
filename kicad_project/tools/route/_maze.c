/* The negotiated router's A* (maze.Grid.route), compiled.
 *
 * Built by kicad_project/tools/setup.sh with the system C compiler into
 * kicad_project/tools/.native/ and loaded by maze.py through ctypes over the
 * grid's own bytearray / array buffers; maze.py falls back to its pure-Python
 * search when the library is missing or a route uses a feature this file does
 * not carry (cross-square headings).
 *
 * It must give the SAME route as the Python search, bit for bit:
 *   - the frontier pops states in the order of Python's heapq of tuples
 *     (f, d, cell, layer, heading): any priority queue does, because distinct
 *     tuples are totally ordered and the comparison here is that order;
 *   - every cost is computed in the Python expression's order, in doubles;
 *     build with -ffp-contract=off and never -ffast-math (no fused
 *     multiply-add, no reassociation).
 * tests/agentic_test.py's router stage routes a fixture both ways and
 * compares the result hashes.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct { double f, d; int32_t c; int8_t li, dr; } Node;

static int node_less(const Node *a, const Node *b) {
    if (a->f != b->f) return a->f < b->f;
    if (a->d != b->d) return a->d < b->d;
    if (a->c != b->c) return a->c < b->c;
    if (a->li != b->li) return a->li < b->li;
    return a->dr < b->dr;
}

typedef struct { Node *v; size_t n, cap; } Heap;

static int heap_push(Heap *h, Node x) {
    if (h->n == h->cap) {
        size_t nc = h->cap ? h->cap * 2 : 4096;
        Node *nv = realloc(h->v, nc * sizeof(Node));
        if (!nv) return -1;
        h->v = nv; h->cap = nc;
    }
    size_t i = h->n++;
    while (i) {
        size_t p = (i - 1) / 2;
        if (!node_less(&x, &h->v[p])) break;
        h->v[i] = h->v[p]; i = p;
    }
    h->v[i] = x;
    return 0;
}

static Node heap_pop(Heap *h) {
    Node top = h->v[0], last = h->v[--h->n];
    size_t i = 0;
    for (;;) {
        size_t l = 2 * i + 1, r = l + 1, m = i;
        const Node *mv = &last;
        if (l < h->n && node_less(&h->v[l], mv)) { m = l; mv = &h->v[l]; }
        if (r < h->n && node_less(&h->v[r], mv)) { m = r; mv = &h->v[r]; }
        if (m == i) break;
        h->v[i] = h->v[m]; i = m;
    }
    if (h->n) h->v[i] = last;
    return top;
}

/* state -> (dist, parent), open addressing on a 64-bit key */
typedef struct { int64_t key; double dist; int64_t par; } Slot;
typedef struct { Slot *s; size_t cap, n; } Map;
#define EMPTY (-1)

static size_t hash64(int64_t k) {
    uint64_t x = (uint64_t)k;
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33;
    return (size_t)x;
}

static int map_init(Map *m, size_t cap) {
    m->cap = cap; m->n = 0;
    m->s = malloc(cap * sizeof(Slot));
    if (!m->s) return -1;
    for (size_t i = 0; i < cap; i++) m->s[i].key = EMPTY;
    return 0;
}

static Slot *map_find(Map *m, int64_t k) {
    size_t i = hash64(k) & (m->cap - 1);
    while (m->s[i].key != EMPTY) {
        if (m->s[i].key == k) return &m->s[i];
        i = (i + 1) & (m->cap - 1);
    }
    return NULL;
}

static int map_grow(Map *m);

static Slot *map_put(Map *m, int64_t k) {
    if ((m->n + 1) * 2 > m->cap && map_grow(m)) return NULL;
    size_t i = hash64(k) & (m->cap - 1);
    while (m->s[i].key != EMPTY) {
        if (m->s[i].key == k) return &m->s[i];
        i = (i + 1) & (m->cap - 1);
    }
    m->s[i].key = k; m->n++;
    return &m->s[i];
}

static int map_grow(Map *m) {
    Map b;
    if (map_init(&b, m->cap * 2)) return -1;
    for (size_t i = 0; i < m->cap; i++)
        if (m->s[i].key != EMPTY) {
            size_t j = hash64(m->s[i].key) & (b.cap - 1);
            while (b.s[j].key != EMPTY) j = (j + 1) & (b.cap - 1);
            b.s[j] = m->s[i]; b.n++;
        }
    free(m->s);
    *m = b;
    return 0;
}

#define KEY(c, li, dr) ((((int64_t)(c)) * 16 + (li)) * 16 + (dr))
#define K_C(k) ((int32_t)((k) / 256))
#define K_L(k) ((int8_t)(((k) / 16) % 16))
#define K_D(k) ((int8_t)((k) % 16))
#define INF 1e18

const char *maze_core_version(void) { return "maze-core 2"; }

/* Returns the path length written to out_* (states from the source to the goal),
 * 0 when there is no path (or the expansion limit was hit), -1 out of memory,
 * -2 when the path is longer than out_cap. */
long maze_astar(
    int nx, int ny, int nl, const int32_t *box,
    const uint8_t **st, const uint8_t **sn, const uint8_t **fx, const uint8_t **dy,
    const float **hs, const uint8_t **cr,
    const uint8_t **mfix, const uint8_t **mdyn, const uint8_t **mcore,
    const uint8_t *vst, const uint8_t *vfx, const uint8_t *vdy, const float *vhs,
    const uint8_t *vmfix, const uint8_t *vmdyn, const uint8_t *via_mask,
    const uint8_t *ok_layer, const double *lc, const double *tcost,
    int diagonal, int soft, int sexact, int fexact,
    double pres, double via_cost, double hk,
    const int32_t *hmap, const uint8_t *goal, int ligm, int goal_dirs,
    const int32_t *srcs, const int8_t *src_layers, int nsrc, int start_dir,
    const float **bias, long limit,
    int32_t *out_c, int8_t *out_l, int8_t *out_d, long out_cap)
{
    static const int moves[8][3] = {{1, 0, 1}, {0, 1, 3}, {-1, 0, 5}, {0, -1, 7},
                                    {1, 1, 2}, {-1, 1, 4}, {-1, -1, 6}, {1, -1, 8}};
    int nmoves = diagonal ? 8 : 4;
    int bi0 = box[0] < 0 ? 0 : box[0], bj0 = box[1] < 0 ? 0 : box[1];
    int bi1 = box[2] > nx - 1 ? nx - 1 : box[2], bj1 = box[3] > ny - 1 ? ny - 1 : box[3];
    Heap h = {0};
    Map m;
    long ret = 0;
    if (map_init(&m, 1 << 16)) return -1;
    for (int s = 0; s < nsrc; s++) {
        Slot *e = map_put(&m, KEY(srcs[s], src_layers[s], start_dir));
        if (!e) { ret = -1; goto done; }
        e->dist = 0.0; e->par = -1;
        Node x = {hmap[srcs[s]] * hk, 0.0, srcs[s], src_layers[s], (int8_t)start_dir};
        if (heap_push(&h, x)) { ret = -1; goto done; }
    }
    int64_t goalkey = -1;
    long n_exp = 0;
    while (h.n) {
        Node top = heap_pop(&h);
        double d = top.d;
        int32_t c = top.c;
        int li = top.li, dr = top.dr;
        Slot *e = map_find(&m, KEY(c, li, dr));
        if (d > (e ? e->dist : INF)) continue;
        if ((ligm >> li & 1) && goal[c] && (!goal_dirs || (goal_dirs >> dr & 1) || dr == 0)) {
            goalkey = KEY(c, li, dr);
            break;
        }
        n_exp++;
        if (n_exp > limit) break;
        int i = c % nx, j = c / nx;
        const uint8_t *s_l = st[li], *f_l = fx[li], *d_l = dy[li], *mf = mfix[li], *md = mdyn[li];
        const uint8_t *sn_l = sn[li], *k_l = cr ? cr[li] : NULL, *mk = mcore ? mcore[li] : NULL;
        const float *h_l = hs[li];
        double step_c = lc[li];
        for (int mv = 0; mv < nmoves; mv++) {
            int di = moves[mv][0], dj = moves[mv][1], nd = moves[mv][2];
            int ni = i + di, nj = j + dj;
            if (!(bi0 <= ni && ni <= bi1 && bj0 <= nj && nj <= bj1)) continue;
            int32_t nc = nj * nx + ni;
            if (s_l[nc]) continue;
            int f = f_l[nc];
            if (f && !(f == 1 && mf[nc])) continue;
            double t;
            if (dr) {
                int tt = abs(nd - dr);
                t = tcost[tt <= 4 ? tt : 8 - tt];
                if (t >= 1e9) continue;
            } else t = 0.0;
            double cost = d + t + ((di && dj) ? step_c * 1.41421356 : step_c);
            long o = d_l[nc];
            if (o && md[nc]) o -= 1;
            if (di && dj) {
                int32_t cc2[2] = {j * nx + ni, nj * nx + i};
                for (int q = 0; q < 2; q++) {
                    int32_t cc = cc2[q];
                    if (s_l[cc] && (!sexact || sn_l[cc])) { o = -1; break; }
                    int ff = f_l[cc];
                    if (ff && !fexact && !(ff == 1 && mf[cc])) { o = -1; break; }
                    long k = k_l[cc];
                    if (k && mk[cc]) k -= 1;
                    o += k;
                }
                if (o < 0) continue;
            }
            if (o) {
                if (!soft) continue;
                cost += pres * (double)o;
            }
            if (soft) cost += (double)h_l[nc];
            if (bias) cost += (double)bias[li][nc];
            int64_t key = KEY(nc, li, nd);
            Slot *x = map_find(&m, key);
            if (cost < (x ? x->dist : INF)) {
                if (!x && !(x = map_put(&m, key))) { ret = -1; goto done; }
                x->dist = cost; x->par = KEY(c, li, dr);
                Node y = {cost + (double)hmap[nc] * hk + ((ligm >> li & 1) ? 0.0 : via_cost), cost, nc, (int8_t)li, (int8_t)nd};
                if (heap_push(&h, y)) { ret = -1; goto done; }
            }
        }
        /* a layer change, where a through via is legal */
        if (vst[c] || (via_mask && !via_mask[c])) continue;
        int vf = vfx[c];
        if (vf && !(vf == 1 && vmfix[c])) continue;
        long vo = vdy[c];
        if (vo && vmdyn[c]) vo -= 1;
        if (vo && !soft) continue;
        for (int nli = 0; nli < nl; nli++) {
            if (nli == li || !ok_layer[nli]) continue;
            if (st[nli][c]) continue;
            int f = fx[nli][c];
            if (f && !(f == 1 && mfix[nli][c])) continue;
            long o = dy[nli][c];
            if (o && mdyn[nli][c]) o -= 1;
            if (o && !soft) continue;
            double cost = d + via_cost;
            if (soft) {
                double add = pres * (double)(vo + o);
                add = add + (double)vhs[c];
                add = add + (double)hs[nli][c];
                cost += add;
            }
            int64_t key = KEY(c, nli, 0);
            Slot *x = map_find(&m, key);
            if (cost < (x ? x->dist : INF)) {
                if (!x && !(x = map_put(&m, key))) { ret = -1; goto done; }
                x->dist = cost; x->par = KEY(c, li, dr);
                Node y = {cost + (double)hmap[c] * hk + ((ligm >> nli & 1) ? 0.0 : via_cost), cost, c, (int8_t)nli, 0};
                if (heap_push(&h, y)) { ret = -1; goto done; }
            }
        }
    }
    if (goalkey < 0) { ret = 0; goto done; }
    /* the path, goal back to the source, then reversed */
    long n = 0;
    for (int64_t k = goalkey; k >= 0;) {
        if (n >= out_cap) { ret = -2; goto done; }
        out_c[n] = K_C(k); out_l[n] = K_L(k); out_d[n] = K_D(k); n++;
        Slot *e2 = map_find(&m, k);
        k = e2 ? e2->par : -1;
    }
    for (long a = 0, b = n - 1; a < b; a++, b--) {
        int32_t tc = out_c[a]; out_c[a] = out_c[b]; out_c[b] = tc;
        int8_t tl = out_l[a]; out_l[a] = out_l[b]; out_l[b] = tl;
        int8_t td = out_d[a]; out_d[a] = out_d[b]; out_d[b] = td;
    }
    ret = n;
done:
    free(h.v);
    free(m.s);
    return ret;
}
