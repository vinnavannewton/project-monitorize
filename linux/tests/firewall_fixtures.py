"""CLI metadata shared by helper command tests, separate from policy evaluation."""
ZONE_INFO = '''home (active)
  target: default
  ports: 50084/tcp 50089/tcp 50090/tcp 50110/tcp 51084/tcp 51089/tcp 51090/tcp 51110/tcp 50098-50110/udp 51098-51110/udp 5353/udp
  icmp-blocks:
  icmp-block-inversion: no
  forward-ports:
  rich rules:
'''


def firewall_response(command):
    if any(argument.startswith('--info-zone=') for argument in command):
        return ZONE_INFO
    if any(argument in command for argument in ('--get-policies', '--get-all-rules', '--get-all-passthroughs')):
        return ''
    if command[-1] == 'ruleset':
        return '{"nftables":[]}'
    return None
