/* config.h — CLI arg parsing and stdin NDJSON command parsing. */
#ifndef P2C_CONFIG_H
#define P2C_CONFIG_H

#include "agent.h"

/* Parse argv into cfg. Returns 0 on success, non-zero on error/--help. */
int  config_parse_args(int argc, char **argv, p2c_config_t *cfg);
void config_print_help(const char *prog);

/* Parse one line of stdin NDJSON into cmd. Returns 0 on success. */
int  config_parse_command(const char *line, size_t len, p2c_cmd_t *cmd);

#endif /* P2C_CONFIG_H */
