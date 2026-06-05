/* ws.c — libwebsockets client + Engine.IO/Socket.IO handshake.
 *
 * Mirrors the reference Python client (app/integrations/platform_ws/
 * p2c_socket.py): recv 0 -> send 40 -> recv 40 -> send 42["list:initialize"]
 * -> stream of 42["list:update",...]; reply 3 to every engine ping 2.
 */
#define _GNU_SOURCE
#include "ws.h"
#include "engineio.h"
#include "events.h"

#include <libwebsockets.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define WS_OUT_QUEUE   8
#define WS_RX_MAX      (256 * 1024)
#define WS_UA          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " \
                       "AppleWebKit/537.36 (KHTML, like Gecko) " \
                       "Chrome/148.0.0.0 Safari/537.36"

struct p2c_ws {
    const p2c_config_t *cfg;
    char  cookie[P2C_COOKIE_MAX];
    ws_callbacks_t cb;
    void *user;

    struct lws_context *ctx;
    struct lws *wsi;

    /* outbound frame queue (small fixed strings) */
    char  out[WS_OUT_QUEUE][64];
    int   out_head, out_tail;

    /* reassembly buffer for fragmented text frames */
    char  rx[WS_RX_MAX];
    size_t rx_len;

    int   connected;
    int   should_stop;
    int   close_code;
};

static void out_push(struct p2c_ws *w, const char *frame)
{
    int next = (w->out_tail + 1) % WS_OUT_QUEUE;
    if (next == w->out_head) return; /* queue full: drop (handshake frames are idempotent enough) */
    snprintf(w->out[w->out_tail], sizeof(w->out[0]), "%s", frame);
    w->out_tail = next;
    if (w->wsi) lws_callback_on_writable(w->wsi);
}

static void handle_frame(struct p2c_ws *w, const char *msg, size_t len)
{
    switch (eio_classify(msg, len)) {
    case EIO_OPEN:
        out_push(w, EIO_FRAME_SOCKET_CONNECT);          /* "40" */
        break;
    case EIO_SOCKET_CONNECT:
        out_push(w, EIO_FRAME_LIST_INIT);               /* 42["list:initialize"] */
        break;
    case EIO_PING:
        out_push(w, EIO_FRAME_PONG);                    /* "3" */
        break;
    case EIO_LIST_UPDATE:
        if (w->cb.on_list_update) w->cb.on_list_update(w->user, msg, len);
        break;
    case EIO_SOCKET_DISCONN:
        w->close_code = 1000;
        if (w->wsi) lws_set_timeout(w->wsi, PENDING_TIMEOUT_CLOSE_ACK, 1);
        break;
    default:
        break;
    }
}

static int cb_ws(struct lws *wsi, enum lws_callback_reasons reason,
                 void *user, void *in, size_t len)
{
    struct p2c_ws *w = (struct p2c_ws *)lws_context_user(lws_get_context(wsi));

    switch (reason) {
    case LWS_CALLBACK_CLIENT_APPEND_HANDSHAKE_HEADER: {
        unsigned char **p = (unsigned char **)in, *end = (*p) + len;
        if (w->cookie[0] &&
            lws_add_http_header_by_name(wsi, (const unsigned char *)"Cookie:",
                (const unsigned char *)w->cookie, (int)strlen(w->cookie), p, end))
            return -1;
        if (lws_add_http_header_by_name(wsi, (const unsigned char *)"Origin:",
                (const unsigned char *)w->cfg->origin, (int)strlen(w->cfg->origin), p, end))
            return -1;
        if (lws_add_http_header_by_name(wsi, (const unsigned char *)"User-Agent:",
                (const unsigned char *)WS_UA, (int)strlen(WS_UA), p, end))
            return -1;
        break;
    }

    case LWS_CALLBACK_CLIENT_ESTABLISHED:
        w->connected = 1;
        w->rx_len = 0;
        if (w->cb.on_connected) w->cb.on_connected(w->user);
        break;

    case LWS_CALLBACK_CLIENT_RECEIVE: {
        /* reassemble fragments */
        if (w->rx_len + len < sizeof(w->rx)) {
            memcpy(w->rx + w->rx_len, in, len);
            w->rx_len += len;
        }
        if (lws_is_final_fragment(wsi) && lws_remaining_packet_payload(wsi) == 0) {
            handle_frame(w, w->rx, w->rx_len);
            w->rx_len = 0;
        }
        break;
    }

    case LWS_CALLBACK_CLIENT_WRITEABLE:
        if (w->out_head != w->out_tail) {
            const char *frame = w->out[w->out_head];
            size_t flen = strlen(frame);
            unsigned char buf[LWS_PRE + 64];
            memcpy(buf + LWS_PRE, frame, flen);
            int n = lws_write(wsi, buf + LWS_PRE, flen, LWS_WRITE_TEXT);
            if (n < (int)flen) return -1;
            w->out_head = (w->out_head + 1) % WS_OUT_QUEUE;
            if (w->out_head != w->out_tail) lws_callback_on_writable(wsi);
        }
        break;

    case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
        w->close_code = 1006;
        w->connected = 0;
        log_warn("ws connection_error: %s", in ? (char *)in : "?");
        return -1;

    case LWS_CALLBACK_CLIENT_CLOSED:
        w->connected = 0;
        if (w->close_code == 0) w->close_code = 1006;
        break;

    default:
        break;
    }
    return 0;
}

static struct lws_protocols PROTOCOLS[] = {
    { "p2c", cb_ws, 0, WS_RX_MAX, 0, NULL, 0 },
    { NULL, NULL, 0, 0, 0, NULL, 0 },  /* terminator */
};

p2c_ws_t *ws_create(const p2c_config_t *cfg, const char *cookie_header,
                    const ws_callbacks_t *cb, void *user)
{
    struct p2c_ws *w = calloc(1, sizeof(*w));
    if (!w) return NULL;
    w->cfg = cfg;
    w->cb = *cb;
    w->user = user;
    if (cookie_header) snprintf(w->cookie, sizeof(w->cookie), "%s", cookie_header);

    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    info.port = CONTEXT_PORT_NO_LISTEN;
    info.protocols = PROTOCOLS;
    info.options = LWS_SERVER_OPTION_DO_SSL_GLOBAL_INIT;
    info.user = w;
    info.fd_limit_per_thread = 16;

    w->ctx = lws_create_context(&info);
    if (!w->ctx) { free(w); return NULL; }
    return w;
}

void ws_run(p2c_ws_t *w, volatile int *running, int max_seconds)
{
    /* parse ws_url into components */
    char url[1024];
    snprintf(url, sizeof(url), "%s", w->cfg->ws_url);
    const char *prot, *addr, *path;
    int port;
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s", url);
    if (lws_parse_uri(tmp, &prot, &addr, &port, &path)) {
        log_err("ws bad url: %s", w->cfg->ws_url);
        return;
    }
    int use_ssl = (strcmp(prot, "wss") == 0 || strcmp(prot, "https") == 0);

    /* lws_parse_uri strips the leading '/' from path; restore it */
    char fullpath[1024];
    snprintf(fullpath, sizeof(fullpath), "/%s", path);

    log_info("ws parsed: addr=%s port=%d ssl=%d path=%.60s", addr, port, use_ssl, fullpath);

    struct lws_client_connect_info ci;
    memset(&ci, 0, sizeof(ci));
    ci.context = w->ctx;
    ci.address = addr;
    ci.port = port;
    ci.path = fullpath;
    ci.host = addr;
    ci.origin = NULL;  /* we add Origin ourselves in APPEND_HANDSHAKE_HEADER */
    ci.protocol = PROTOCOLS[0].name;
    ci.ssl_connection = use_ssl ? (LCCSCF_USE_SSL | LCCSCF_SKIP_SERVER_CERT_HOSTNAME_CHECK) : 0;
    ci.pwsi = &w->wsi;

    w->connected = 0;
    w->close_code = 0;
    w->out_head = w->out_tail = 0;
    w->rx_len = 0;

    log_info("ws lws_client_connect_via_info calling addr=%s", addr);
    if (!lws_client_connect_via_info(&ci)) {
        log_err("ws connect failed (lws returned NULL): %s", w->cfg->ws_url);
        return;
    }

    log_info("ws connecting: %s", w->cfg->ws_url);

    time_t deadline = max_seconds > 0 ? time(NULL) + max_seconds : 0;
    /* Break out if still not connected after 15s — Cloudflare may hang the
     * HTTP Upgrade on bot-detection.  lws_service loops silently in that case. */
    time_t connect_deadline = time(NULL) + 15;
    while (*running && w->close_code == 0) {
        int sr = lws_service(w->ctx, 100);
        if (sr < 0) { log_warn("ws lws_service returned %d, breaking", sr); break; }
        if (!w->connected && time(NULL) >= connect_deadline) {
            log_err("ws connect timeout (15s): %s", w->cfg->ws_url);
            break;
        }
        if (deadline && time(NULL) >= deadline) {
            log_info("ws refresh deadline reached, reconnecting");
            break;
        }
    }
    log_info("ws loop exited connected=%d close_code=%d", w->connected, w->close_code);
    if (w->cb.on_disconnected) w->cb.on_disconnected(w->user, w->close_code ? w->close_code : 1000);
}

void ws_destroy(p2c_ws_t *w)
{
    if (!w) return;
    if (w->ctx) lws_context_destroy(w->ctx);
    free(w);
}
