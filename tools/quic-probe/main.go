// A short-lived TLS certificate probe. It never sends Hysteria credentials.
package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"time"

	quic "github.com/apernet/quic-go"
	"golang.org/x/crypto/blake2b"
)

type request struct {
	Address      string `json:"address"`
	Port         int    `json:"port"`
	SNI          string `json:"sni"`
	ObfsPassword string `json:"obfs_password"`
}
type result struct {
	Status string `json:"status"`
	Pin    string `json:"pin,omitempty"`
}

// Salamander wire format: 8 random salt bytes followed by the payload XORed
// with BLAKE2b-256(password || salt). This wrapper is used only for the probe.
type maskedConn struct {
	net.PacketConn
	password []byte
}

func (c *maskedConn) key(salt []byte) [32]byte {
	data := make([]byte, 0, len(c.password)+8)
	data = append(data, c.password...)
	return blake2b.Sum256(append(data, salt...))
}
func (c *maskedConn) WriteTo(p []byte, a net.Addr) (int, error) {
	b := make([]byte, len(p)+8)
	if _, err := rand.Read(b[:8]); err != nil {
		return 0, err
	}
	k := c.key(b[:8])
	for i := range p {
		b[i+8] = p[i] ^ k[i%32]
	}
	n, err := c.PacketConn.WriteTo(b, a)
	if err != nil {
		return 0, err
	}
	if n != len(b) {
		return 0, io.ErrShortWrite
	}
	return len(p), nil
}
func (c *maskedConn) ReadFrom(p []byte) (int, net.Addr, error) {
	b := make([]byte, 65536)
	for {
		n, a, err := c.PacketConn.ReadFrom(b)
		if err != nil {
			return 0, a, err
		}
		if n <= 8 {
			continue
		}
		if n-8 > len(p) {
			return 0, a, io.ErrShortBuffer
		}
		k := c.key(b[:8])
		for i := 0; i < n-8; i++ {
			p[i] = b[i+8] ^ k[i%32]
		}
		return n - 8, a, nil
	}
}

func checkCertificate(cs tls.ConnectionState, roots *x509.CertPool, sni string) result {
	if len(cs.PeerCertificates) == 0 {
		return result{Status: "error"}
	}
	leaf := cs.PeerCertificates[0]
	pin := sha256.Sum256(leaf.Raw)
	intermediates := x509.NewCertPool()
	for _, cert := range cs.PeerCertificates[1:] {
		intermediates.AddCert(cert)
	}
	_, err := leaf.Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates, DNSName: sni})
	status := "trusted"
	if err != nil {
		var unknown x509.UnknownAuthorityError
		status = "invalid"
		if errors.As(err, &unknown) {
			// Unknown roots must not conceal an expired leaf or a wrong name.
			now := time.Now()
			if !now.Before(leaf.NotBefore) && !now.After(leaf.NotAfter) && leaf.VerifyHostname(sni) == nil {
				status = "private"
			}
		}
	}
	return result{Status: status, Pin: hex.EncodeToString(pin[:])}
}

func probe(ctx context.Context, req request, roots *x509.CertPool) result {
	if req.Port < 1 || req.Port > 65535 || req.Address == "" || req.SNI == "" || len(req.ObfsPassword) > 4096 || (req.ObfsPassword != "" && len(req.ObfsPassword) < 4) {
		return result{Status: "error"}
	}
	ips, err := net.DefaultResolver.LookupIPAddr(ctx, req.Address)
	if err != nil || len(ips) == 0 {
		return result{Status: "error"}
	}
	chosen := ips[0]
	for _, ip := range ips {
		if ip.IP.To4() != nil {
			chosen = ip
			break
		}
	}
	a := &net.UDPAddr{IP: chosen.IP, Zone: chosen.Zone, Port: req.Port}
	network, local := "udp4", "0.0.0.0:0"
	if a.IP.To4() == nil {
		network, local = "udp6", "[::]:0"
	}
	var lc net.ListenConfig
	conn, err := lc.ListenPacket(ctx, network, local)
	if err != nil {
		return result{Status: "error"}
	}
	defer conn.Close()
	if req.ObfsPassword != "" {
		conn = &maskedConn{PacketConn: conn, password: []byte(req.ObfsPassword)}
	}
	transport := &quic.Transport{Conn: conn}
	defer transport.Close()
	// Verification is evaluated below and returned to the caller. No auth data
	// or application request is transmitted, even when CA validation fails.
	config := &tls.Config{ServerName: req.SNI, NextProtos: []string{"h3"}, InsecureSkipVerify: true}
	c, err := transport.Dial(ctx, a, config, &quic.Config{HandshakeIdleTimeout: 5 * time.Second, MaxIdleTimeout: 6 * time.Second})
	if err != nil {
		return result{Status: "error"}
	}
	defer c.CloseWithError(0, "")
	return checkCertificate(c.ConnectionState().TLS, roots, req.SNI)
}

func main() {
	var req request
	dec := json.NewDecoder(io.LimitReader(os.Stdin, 16385))
	dec.DisallowUnknownFields() // In particular, no auth/password field allowed.
	var out result
	if dec.Decode(&req) != nil {
		out.Status = "error"
	} else {
		roots, _ := x509.SystemCertPool()
		if roots == nil {
			roots = x509.NewCertPool()
		}
		ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
		out = probe(ctx, req, roots)
		cancel()
	}
	json.NewEncoder(os.Stdout).Encode(out)
}
