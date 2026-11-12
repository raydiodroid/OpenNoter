#!/bin/sh
# Run as root on the Docker host before starting media-broker. Scope is exclusively
# the dedicated worker subnet; no existing application or host egress rules change.
set -eu
SUBNET=172.16.249.0/24
NETWORK=opennoter-media-internal
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  docker network create --internal --driver bridge --subnet "$SUBNET" \
    --opt com.docker.network.bridge.enable_icc=false "$NETWORK" >/dev/null
fi
test "$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$NETWORK")" = "$SUBNET"
test "$(docker network inspect -f '{{index .Options "com.docker.network.bridge.enable_icc"}}' "$NETWORK")" = false
test "$(docker network inspect -f '{{.Internal}}' "$NETWORK")" = true
iptables -N OPENNOTER-MEDIA 2>/dev/null || true
# Insert an unconditional temporary block while rebuilding the scoped chain.
iptables -I DOCKER-USER 1 -s "$SUBNET" -j DROP
iptables -F OPENNOTER-MEDIA
iptables -A OPENNOTER-MEDIA -s 172.16.249.2 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OPENNOTER-MEDIA -d 172.16.249.2 -p tcp --dport 3128 -j ACCEPT
for destination in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 \
  169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 \
  198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4; do
  iptables -A OPENNOTER-MEDIA -d "$destination" -j REJECT
done
iptables -A OPENNOTER-MEDIA -j RETURN
iptables -C INPUT -s "$SUBNET" -j REJECT 2>/dev/null || iptables -I INPUT 1 -s "$SUBNET" -j REJECT
iptables -C DOCKER-USER -s "$SUBNET" -j OPENNOTER-MEDIA 2>/dev/null || \
  iptables -I DOCKER-USER 2 -s "$SUBNET" -j OPENNOTER-MEDIA
while iptables -C DOCKER-USER -s "$SUBNET" -j DROP 2>/dev/null; do
  iptables -D DOCKER-USER -s "$SUBNET" -j DROP
done
