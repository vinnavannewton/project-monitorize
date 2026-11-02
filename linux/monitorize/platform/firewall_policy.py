"""Conservative reachability checks for the live iptables UFW rule graph.

Only prove permission when every possible path for a normal Monitorize packet
accepts. Unknown matches branch both ways; unknown targets never imply access.
No rules are inserted, reordered or removed by this module.
"""
import ipaddress
import shlex
from pathlib import Path


def parse_save(text):
    tables = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('*'):
            current = tables.setdefault(line[1:], {})
        elif line == 'COMMIT':
            current = None
        elif current is None:
            raise ValueError('Unrecognized firewall ruleset.')
        elif line.startswith(':'):
            name, policy, *_ = line[1:].split()
            current[name] = {'policy': policy, 'rules': []}
        elif line.startswith('-A '):
            words = shlex.split(line)
            if words[1] not in current:
                raise ValueError('Firewall rule references an undeclared chain.')
            current[words[1]]['rules'].append(words[2:])
        else:
            raise ValueError('Unsupported firewall ruleset entry.')
    if current is not None:
        raise ValueError('Incomplete firewall ruleset.')
    return tables


def port_matches(spec, value):
    if value is None:
        return None
    for item in spec.split(','):
        bounds = item.replace('-', ':').split(':')
        if len(bounds) not in (1, 2):
            raise ValueError('Unsupported firewall port range.')
        low, high = int(bounds[0] or 0), int(bounds[-1] or 65535)
        if low <= value <= high:
            return True
    return False


def address_matches(spec, packet, source):
    network = ipaddress.ip_network(spec, strict=False)
    if network.version != packet['family']:
        return False
    if network.prefixlen == 0:
        return True
    if not source and packet['multicast']:
        return ipaddress.ip_address('224.0.0.251' if packet['family'] == 4 else 'ff02::fb') in network
    # Monitorize LAN traffic is neither loopback nor multicast unless explicitly
    # represented as mDNS. Other address restrictions require a review.
    if network.is_loopback or network.is_multicast:
        return False
    return None


def match_rule(words, packet):
    results = []
    target = None
    goto = False
    index = 0
    negate = False
    while index < len(words):
        option = words[index]
        index += 1
        if option == '!':
            negate = True
            continue
        # --syn has no operand; --tcp-flags has two.
        arity = 0 if option == '--syn' else 2 if option == '--tcp-flags' else 1
        args = words[index:index + arity]
        index += arity
        if len(args) != arity:
            raise ValueError('Incomplete firewall rule.')
        value = args[0] if args else ''
        if option in ('-j', '--jump', '-g', '--goto'):
            target, goto = value, option in ('-g', '--goto')
            # Target options cannot introduce packet matches. Known harmless
            # logging/REJECT options are irrelevant to permission analysis.
            if index < len(words) and target not in ('LOG', 'NFLOG', 'REJECT'):
                results.append(None)
            break
        if option in ('-m', '--match'):
            if value not in ('tcp', 'udp', 'multiport', 'conntrack', 'state', 'addrtype', 'comment', 'limit', 'hl', 'rt', 'icmp6', 'icmp'):
                results.append(None)
            continue
        if option == '--comment':
            continue
        if option in ('-p', '--protocol'):
            result = value in (packet['protocol'], 'all', '0', '6' if packet['protocol'] == 'tcp' else '17')
        elif option in ('--dport', '--destination-port', '--dports'):
            result = port_matches(value, packet['dport'])
        elif option in ('--sport', '--source-port', '--sports'):
            result = port_matches(value, packet['sport'])
        elif option in ('--ctstate', '--state'):
            result = packet['state'] in value.split(',')
        elif option in ('-i', '--in-interface', '-o', '--out-interface'):
            result = False if value == 'lo' else None
        elif option in ('-s', '--source', '-d', '--destination'):
            result = address_matches(value, packet, option in ('-s', '--source'))
        elif option == '--dst-type':
            expected = 'MULTICAST' if packet['multicast'] else 'LOCAL' if packet['hook'] == 'INPUT' else 'UNICAST'
            result = expected in value.split(',')
        elif option in ('--icmp-type', '--icmpv6-type'):
            result = False
        elif option == '--rt-type':
            result = False  # Normal streaming packets have no IPv6 routing header.
        elif option == '--syn':
            result = packet['protocol'] == 'tcp' and packet['state'] == 'NEW'
        else:
            result = None
        results.append(None if result is None else not result if negate else result)
        negate = False
    if negate:
        raise ValueError('Incomplete inverted firewall match.')
    match = False if False in results else None if None in results else True
    return match, target, goto


def verdicts(chains, chain, packet, stack=(), budget=None):
    if budget is None:
        budget = [10000]
    if chain in stack or len(stack) > 32:
        return {'UNKNOWN'}
    if chain not in chains:
        return {'UNKNOWN'}
    outcomes = set()
    continuing = True
    for words in chains[chain]['rules']:
        budget[0] -= 1
        if budget[0] < 0:
            return {'UNKNOWN'}
        if not continuing:
            break
        match, target, goto = match_rule(words, packet)
        if match is False:
            continue
        if target in ('LOG', 'NFLOG', 'MARK', 'CONNMARK', None):
            # MARK/CONNMARK update metadata and continue traversal; they are
            # neither user-chain jumps nor verdicts. Mark-dependent matches
            # elsewhere remain unknown and still branch both ways, so a later
            # conditional or unconditional deny cannot be hidden by marking.
            branch = {'RETURN'}
        elif target in ('ACCEPT', 'DROP', 'REJECT', 'RETURN'):
            branch = {target}
            # An explicit RETURN exits this chain; it does not mean continue.
            if target == 'RETURN':
                outcomes.add('RETURN')
                continuing = match is None
                continue
        else:
            branch = verdicts(chains, target, packet, (*stack, chain), budget)
        outcomes.update(branch - {'RETURN'})
        if goto and 'RETURN' in branch:
            outcomes.add('RETURN')
        continuing = match is None or ('RETURN' in branch and not goto)
    if continuing:
        outcomes.add('RETURN')
    return outcomes


def verify_save(text, family, ports, *, require_filter=True):
    """Check all incoming ports, replies, and mDNS queries in all live tables."""
    tables = parse_save(text)
    if require_filter and ('filter' not in tables or not {'INPUT', 'OUTPUT'}.issubset(tables['filter'])
            or any(tables['filter'][hook]['policy'] not in ('ACCEPT', 'DROP') for hook in ('INPUT', 'OUTPUT'))):
        raise ValueError('The active UFW filter table could not be read.')
    packets = []
    for spec in ports:
        number, protocol = spec.split('/')
        bounds = number.split('-')
        for port in range(int(bounds[0]), int(bounds[-1]) + 1):
            for state in ('NEW', 'ESTABLISHED'):
                for hook in ('INPUT', 'OUTPUT'):
                    if hook == 'OUTPUT' and protocol == 'tcp' and state == 'NEW':
                        continue  # Replies to an accepted TCP connection are established.
                    packets.append(dict(family=family, protocol=protocol, state=state, hook=hook,
                                        dport=port if hook == 'INPUT' else None,
                                        sport=port if hook == 'OUTPUT' else None, multicast=False))
    for hook in ('INPUT', 'OUTPUT'):
        packets.append(dict(family=family, protocol='udp', state='NEW', hook=hook,
                            dport=5353, sport=5353, multicast=True))
    for table, chains in tables.items():
        for packet in packets:
            # These hooks can affect traffic delivered to/from this machine.
            hooks = ('PREROUTING', 'INPUT') if packet['hook'] == 'INPUT' else ('OUTPUT', 'POSTROUTING')
            for hook in hooks:
                if hook not in chains:
                    continue
                outcomes = verdicts(chains, hook, packet)
                if 'RETURN' in outcomes:
                    outcomes = (outcomes - {'RETURN'}) | {chains[hook]['policy']}
                if outcomes != {'ACCEPT'}:
                    port = packet['dport'] if packet['hook'] == 'INPUT' else packet['sport']
                    raise RuntimeError(f'IPv{family} {table}/{hook} may block {port}/{packet["protocol"]} '
                                       f'({packet["state"]}). Existing policy was preserved; review the rule precedence.')
    return True


def ipv6_enabled(*, root=Path('/proc/sys/net/ipv6/conf')):
    if not root.exists():
        return False
    # all/default alone do not prove that an interface has no explicit override.
    values = list(root.glob('*/disable_ipv6'))
    return not values or not all(path.read_text().strip() == '1' for path in values)


def verify_ufw(command_path, run, ports, *, trusted_nft_tables=()):
    """Read active IPv4/IPv6 backends; never trust a profile-name status line."""
    expected = set()
    ipv6 = ipv6_enabled()
    for family, name in ((4, 'iptables-save'), (6, 'ip6tables-save')):
        if family == 6 and not ipv6:
            continue
        executable = command_path('/usr/sbin/' + name, '/usr/bin/' + name, '/sbin/' + name)
        if not executable:
            raise RuntimeError('UFW policy verification requires ' + name + '.')
        text = run([executable])
        verify_save(text, family, ports)
        for table, chains in parse_save(text).items():
            for chain in chains:
                expected.add(('ip' if family == 4 else 'ip6', table, chain))
        # A separate legacy backend can coexist with nft-backed iptables.
        legacy = command_path('/usr/sbin/' + name.replace('-save', '-legacy-save'),
                              '/usr/bin/' + name.replace('-save', '-legacy-save'))
        if legacy and legacy != executable:
            legacy_text = run([legacy])
            if legacy_text.strip() and parse_save(legacy_text):
                verify_save(legacy_text, family, ports)
    nft = command_path('/usr/sbin/nft', '/usr/bin/nft', '/sbin/nft')
    if not nft:
        raise RuntimeError('UFW policy verification requires nftables to inspect coexisting rules.')
    import json
    value = json.loads(run([nft, '--json', 'list', 'ruleset']))
    if not isinstance(value, dict) or not isinstance(value.get('nftables'), list):
        raise ValueError('Could not inspect coexisting nftables rules.')
    for entry in value['nftables']:
        chain = entry.get('chain', {})
        if (chain.get('family'), chain.get('table')) in trusted_nft_tables:
            continue
        if chain.get('family') == 'ip6' and not ipv6:
            continue
        if chain.get('hook') in ('ingress', 'prerouting', 'input', 'output', 'postrouting'):
            identity = (chain.get('family'), chain.get('table'), chain.get('name'))
            if identity not in expected:
                raise RuntimeError('Coexisting nftables chain ' + '/'.join(str(part) for part in identity)
                                   + ' needs policy review. UFW permission alone cannot verify access.')
    families = 'IPv4/IPv6' if ipv6 else 'IPv4 (IPv6 disabled in the kernel)'
    return 'UFW live ' + families + ' streaming, reply and discovery policies verified.'


def info_fields(text):
    fields = {}
    previous = None
    for line in text.splitlines()[1:]:
        value = line.strip()
        if value.startswith('rule '):
            if 'rich rules' not in fields:
                raise ValueError('Unexpected firewalld rich rule output.')
            fields['rich rules'].append(value)
        elif value and (line.startswith('\t') or line.startswith('    ')):
            if previous != 'forward-ports':
                raise ValueError('Unrecognized firewalld configuration continuation.')
            fields[previous] = (fields[previous] + ' ' + value).strip()
        elif value:
            key, separator, content = value.partition(':')
            if not separator:
                raise ValueError('Unrecognized firewalld configuration output.')
            fields[key] = [] if key == 'rich rules' else content.strip()
            previous = key
    return fields


def overlap_ports(specs, required):
    for spec in specs:
        number, protocol = spec.split('/')
        for needed in required:
            values, needed_protocol = needed.split('/')
            if protocol != needed_protocol:
                continue
            bounds = values.split('-')
            if any(port_matches(number, value) for value in range(int(bounds[0]), int(bounds[-1]) + 1)):
                return True
    return False


CRITICAL_ICMPV6 = {'neighbour-advertisement', 'neighbour-solicitation', 'router-advertisement',
                   'router-solicitation', 'packet-too-big', 'destination-unreachable', 'time-exceeded',
                   'parameter-problem'}


def rich_rule_conflicts(rule, ports, lookup_service, *, direction='input', zone=False):
    words = shlex.split(rule)
    if not words or words[0] != 'rule':
        raise ValueError('Unrecognized firewalld rich rule.')
    priority = next((int(word.split('=', 1)[1]) for word in words if word.startswith('priority=')), 0)
    # Positive-priority zone rules run after the broad port permission. Policy
    # rules have a separate ordering and cannot use this zone-only shortcut.
    late_zone = zone and priority > 0 and not any(element in words for element in ('mark', 'forward-port', 'masquerade'))
    if not any(word in words for word in ('drop', 'reject', 'mark', 'forward-port', 'masquerade', 'icmp-block')):
        return False
    groups = {}
    current = 'rule'
    for word in words[1:]:
        if '=' in word:
            key, value = word.split('=', 1)
            groups.setdefault(current, {})[key] = value
        elif word not in ('not',):
            current = word
    if groups.get('rule', {}).get('family') == 'ipv6' and not ipv6_enabled():
        return False
    if 'icmp-type' in groups or 'icmp-block' in groups:
        element = groups.get('icmp-type', groups.get('icmp-block', {}))
        return element.get('name') in CRITICAL_ICMPV6 and ipv6_enabled()
    if 'protocol' in groups:
        protocol = groups['protocol'].get('value')
        if protocol in ('tcp', 'udp', '6', '17'):
            return not late_zone
        return protocol in ('icmp', '1', 'ipv6-icmp', '58')
    for element in ('port', 'source-port'):
        if element in groups:
            values = groups[element]
            if values.get('protocol') not in ('tcp', 'udp'):
                return False
            if late_zone:
                return False
            if (element == 'source-port') != (direction == 'output'):
                return True  # Peer ports are unrestricted by setup's contract.
            return overlap_ports([values['port'] + '/' + values['protocol']], ports)
    if 'service' in groups:
        service = lookup_service(groups['service']['name'])
        if not {'ports', 'protocols'}.issubset(service):
            raise ValueError('Incomplete firewalld service information.')
        if any(protocol in ('icmp', '1', 'ipv6-icmp', '58') for protocol in service.get('protocols', '').split()):
            return True
        if service.get('includes'):
            return True  # Unknown nested service constraints require review.
        if late_zone:
            return False
        if any(protocol in ('tcp', 'udp', '6', '17') for protocol in service.get('protocols', '').split()):
            return True
        specs = service.get('ports', '').split()
        source_specs = service.get('source-ports', '').split()
        if source_specs and (direction == 'input' or overlap_ports(source_specs, ports)):
            return True
        return bool(specs) if direction == 'output' else overlap_ports(specs, ports)
    return True  # A source/destination-only drop can affect a legitimate client.


def verify_firewalld(executable, zones, command_path, run, ports):
    """Verify managed policy and expose other filtering hooks without altering it."""
    for permanent in (False, True):
        mode = ['--permanent'] if permanent else []
        label = 'persistent' if permanent else 'runtime'
        def query(*arguments):
            return run([executable, *mode, *arguments])
        def service(name):
            # Names come from firewalld itself and remain a single option value.
            if not name or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for char in name):
                raise ValueError('Unsupported firewalld service name.')
            return info_fields(query('--info-service=' + name))
        for zone_name in zones:
            fields = info_fields(query('--info-zone=' + zone_name))
            if not {'target', 'ports', 'rich rules', 'icmp-blocks', 'forward-ports'}.issubset(fields):
                raise ValueError('Incomplete firewalld zone information.')
            for spec in ports:
                number, protocol = spec.split('/')
                bounds = number.split('-')
                if any(not overlap_ports(fields['ports'].split(), [str(value) + '/' + protocol])
                       for value in range(int(bounds[0]), int(bounds[-1]) + 1)):
                    raise RuntimeError('Firewalld ' + label + ' ports changed in ' + zone_name + ' during policy verification.')
            if fields.get('forward-ports'):
                raise RuntimeError('Firewalld ' + label + ' port forwarding in ' + zone_name + ' needs policy review before access can be verified.')
            blocked = set(fields.get('icmp-blocks', '').split())
            if ipv6_enabled() and (blocked & CRITICAL_ICMPV6 or
                    (fields.get('icmp-block-inversion') == 'yes' and not CRITICAL_ICMPV6.issubset(blocked))):
                raise RuntimeError('Firewalld ' + label + ' ICMP policy in ' + zone_name + ' can block IPv6 discovery or connectivity.')
            for rule in fields['rich rules']:
                if rich_rule_conflicts(rule, ports, service, zone=True):
                    raise RuntimeError('Firewalld ' + label + ' rule in ' + zone_name + ' may block Monitorize: ' + rule)
        for policy_name in query('--get-policies').split():
            fields = info_fields(query('--info-policy=' + policy_name))
            if fields.get('disable') == 'yes':
                continue
            if not {'priority', 'target', 'ingress-zones', 'egress-zones', 'rich rules'}.issubset(fields):
                raise ValueError('Incomplete firewalld policy information.')
            ingress = set(fields.get('ingress-zones', '').split())
            egress = set(fields.get('egress-zones', '').split())
            incoming = 'HOST' in egress and bool(ingress & (set(zones) | {'ANY'}))
            outgoing = 'HOST' in ingress and bool(egress & (set(zones) | {'ANY'}))
            if not incoming and not outgoing:
                continue
            # Late policies can still block ICMP/discovery even after zone TCP/
            # UDP permissions. Only those specific late rich matches are safe.
            if fields['target'] not in ('ACCEPT', 'CONTINUE') or fields.get('forward-ports'):
                raise RuntimeError('Firewalld ' + label + ' host policy ' + policy_name + ' needs review before Monitorize access can be verified.')
            if set(fields.get('icmp-blocks', '').split()) & CRITICAL_ICMPV6 and ipv6_enabled():
                raise RuntimeError('Firewalld host policy ' + policy_name + ' may block IPv6 connectivity.')
            for rule in fields['rich rules']:
                late_input = incoming and not outgoing and int(fields['priority']) > 0
                checked_rule = rule
                if late_input:
                    # Policy priority orders the whole policy after zone ports;
                    # rich priority alone cannot move it before those ports.
                    checked_rule = 'rule priority="1" ' + ' '.join(shlex.quote(word) for word in shlex.split(rule)[1:]
                                                                  if not word.startswith('priority='))
                if rich_rule_conflicts(checked_rule, ports, service, direction='output' if outgoing else 'input', zone=late_input):
                    raise RuntimeError('Firewalld ' + label + ' host policy ' + policy_name + ' may block Monitorize: ' + rule)
        if query('--direct', '--get-all-rules').strip() or query('--direct', '--get-all-passthroughs').strip():
            raise RuntimeError('Firewalld ' + label + ' direct rules require policy review; port presence cannot verify their precedence.')
    nft = command_path('/usr/sbin/nft', '/usr/bin/nft')
    if not nft:
        raise RuntimeError('nftables is required to verify coexisting firewall policy.')
    import json
    ruleset = json.loads(run([nft, '--json', 'list', 'ruleset']))
    compat = {}
    for entry in ruleset['nftables']:
        chain = entry.get('chain', {})
        if chain.get('hook') not in ('ingress', 'prerouting', 'input', 'output', 'postrouting'):
            continue
        if (chain.get('family'), chain.get('table')) == ('inet', 'firewalld'):
            continue
        family = chain.get('family')
        if family in ('ip', 'ip6') and chain.get('name') in ('PREROUTING', 'INPUT', 'OUTPUT', 'POSTROUTING'):
            if family not in compat:
                name = 'iptables-save' if family == 'ip' else 'ip6tables-save'
                reader = command_path('/usr/sbin/' + name, '/usr/bin/' + name)
                if not reader:
                    raise RuntimeError('Coexisting iptables policy requires ' + name + ' for verification.')
                text = run([reader])
                verify_save(text, 4 if family == 'ip' else 6, ports, require_filter=False)
                compat[family] = parse_save(text)
            if chain.get('name') in compat[family].get(chain.get('table'), {}):
                continue
        raise RuntimeError('Coexisting nftables filtering chain ' + str(chain.get('table')) + '/' + str(chain.get('name')) + ' requires policy review.')
    return 'Firewalld runtime and persistent streaming/discovery policy verified in: ' + ', '.join(zones) + '.'
