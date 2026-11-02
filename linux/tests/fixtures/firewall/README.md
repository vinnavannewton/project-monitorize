# UFW live export fixtures

The two exports were captured from Fedora 44, UFW 0.35-39.fc44 and
iptables-nft 1.8.11, inside a private network namespace in a disposable test VM.
UFW used its shipped before/after chains, its initial SSH/mDNS rules and the
Monitorize application profile. The VM remained SELinux Enforcing.

The component check compared policy analysis with real TCP connections on
50089 over IPv4 and IPv6, including earlier/later denies, hidden before-chain
rules, an IPv6-only drop, blocked replies and a coexisting native nftables chain.
This is component evidence, not complete native-package or Moonlight acceptance.

The rule order and visibility limits of `ufw status` are documented in the
[UFW framework manual](https://manpages.ubuntu.com/manpages/noble/man8/ufw-framework.8.html).
