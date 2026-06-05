/* main.c — entry point: arg parse, command loop, account registry, heartbeat.
 *
 * The hot path lives in account.c (WS thread) / taker.c (take thread). main
 * just parses CLI args, owns the account registry, and routes stdin NDJSON
 * commands to it (docs/C_AGENT_SPEC_RU.md §4.6).
 */
#include "agent.h"
#include "config.h"
#include "events.h"
#include "account.h"

#include <curl/curl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile int g_running = 1;
static p2c_registry_t *g_registry = NULL;

static void on_signal(int sig) { (void)sig; g_running = 0; }

static void *heartbeat_thread(void *arg)
{
    (void)arg;
    while (g_running) {
        for (int i = 0; i < 50 && g_running; ++i) {
            struct timespec ts = { .tv_sec = 0, .tv_nsec = 100000000 }; /* 100ms */
            nanosleep(&ts, NULL);
        }
        if (!g_running) break;
        if (g_registry) registry_heartbeat(g_registry);
    }
    return NULL;
}

static void handle_command(const p2c_cmd_t *cmd)
{
    switch (cmd->kind) {
    case P2C_CMD_ADD_ACCOUNT:
        registry_add_account(g_registry, cmd->account, &cmd->session,
                             cmd->has_filter ? &cmd->filter : NULL);
        break;
    case P2C_CMD_SESSION:
        registry_set_session(g_registry, cmd->account, &cmd->session);
        log_info("cmd session account=%s", cmd->account);
        break;
    case P2C_CMD_FILTER:
        registry_set_filter(g_registry, cmd->account, &cmd->filter);
        break;
    case P2C_CMD_MODE:
        registry_set_mode(g_registry, cmd->account, cmd->mode);
        break;
    case P2C_CMD_REMOVE_ACCOUNT:
        registry_remove(g_registry, cmd->account);
        break;
    case P2C_CMD_SHUTDOWN:
        log_info("cmd shutdown");
        g_running = 0;
        break;
    default:
        log_warn("cmd unknown");
        break;
    }
}

int main(int argc, char **argv)
{
    p2c_config_t cfg;
    int rc = config_parse_args(argc, argv, &cfg);
    if (rc != 0) return rc == 1 ? 0 : rc;

    ev_init();
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);
    curl_global_init(CURL_GLOBAL_DEFAULT);

    log_info("p2c_agent start ws=%s base=%s redis=%s impersonate=%s realtime=%d",
             cfg.ws_url, cfg.base_url, cfg.redis_url ? cfg.redis_url : "-",
             cfg.impersonate, cfg.realtime);

    g_registry = registry_create(&cfg, &g_running);
    if (!g_registry) { log_err("registry create failed"); return 1; }

    pthread_t hb;
    pthread_create(&hb, NULL, heartbeat_thread, NULL);

    char *line = NULL;
    size_t cap = 0;
    ssize_t n;
    while (g_running && (n = getline(&line, &cap, stdin)) != -1) {
        if (n > 0 && line[n - 1] == '\n') line[--n] = '\0';
        if (n == 0) continue;
        p2c_cmd_t cmd;
        if (config_parse_command(line, (size_t)n, &cmd) == 0) handle_command(&cmd);
        else log_warn("bad command line (not JSON)");
    }
    free(line);

    g_running = 0;
    pthread_join(hb, NULL);
    registry_destroy(g_registry);
    curl_global_cleanup();
    log_info("p2c_agent stop");
    return 0;
}
