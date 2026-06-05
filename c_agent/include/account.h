/* account.h — per-account orchestration and the account registry.
 *
 * Each account runs its own WS thread (detect) and take thread (send) with its
 * own session, warm take connection, per-account dedup set and slots
 * (docs/C_AGENT_SPEC_RU.md §2.2/§2.3). The registry is the handle main.c uses
 * to apply stdin commands.
 */
#ifndef P2C_ACCOUNT_H
#define P2C_ACCOUNT_H

#include "agent.h"

typedef struct p2c_registry p2c_registry_t;

p2c_registry_t *registry_create(const p2c_config_t *cfg, volatile int *global_running);
void registry_destroy(p2c_registry_t *reg);

/* Commands (account == "" applies to all where meaningful). */
int  registry_add_account(p2c_registry_t *reg, const char *account,
                          const p2c_session_t *session, const p2c_filter_t *filter);
int  registry_set_session(p2c_registry_t *reg, const char *account,
                          const p2c_session_t *session);
int  registry_set_filter(p2c_registry_t *reg, const char *account,
                         const p2c_filter_t *filter);
int  registry_set_mode(p2c_registry_t *reg, const char *account, p2c_mode_t mode);
int  registry_remove(p2c_registry_t *reg, const char *account);

/* Emit one heartbeat line covering all accounts. */
void registry_heartbeat(p2c_registry_t *reg);

#endif /* P2C_ACCOUNT_H */
