package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"math/big"
	"net"
	"testing"
	"time"

	quic "github.com/apernet/quic-go"
)

func certificate(t *testing.T, expired bool) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	until := time.Now().Add(time.Hour)
	if expired {
		until = time.Now().Add(-time.Hour)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "probe.example"}, DNSNames: []string{"probe.example"}, NotBefore: time.Now().Add(-24 * time.Hour), NotAfter: until, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key, Leaf: leaf}
}

func TestProbeTLSAndSalamander(t *testing.T) {
	for _, obfs := range []string{"", "test-salamander"} {
		for _, policy := range []string{"private", "trusted", "wrong-name", "expired"} {
			t.Run(policy+"/"+obfs, func(t *testing.T) {
				cert := certificate(t, policy == "expired")
				conn, err := net.ListenPacket("udp4", "127.0.0.1:0")
				if err != nil {
					t.Fatal(err)
				}
				defer conn.Close()
				if obfs != "" {
					conn = &maskedConn{PacketConn: conn, password: []byte(obfs)}
				}
				transport := &quic.Transport{Conn: conn}
				defer transport.Close()
				listener, err := transport.Listen(&tls.Config{Certificates: []tls.Certificate{cert}, NextProtos: []string{"h3"}}, &quic.Config{})
				if err != nil {
					t.Fatal(err)
				}
				defer listener.Close()
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				defer cancel()
				go func() { _, _ = listener.Accept(ctx) }()
				roots := x509.NewCertPool()
				if policy == "trusted" {
					roots.AddCert(cert.Leaf)
				}
				sni := "probe.example"
				if policy == "wrong-name" {
					sni = "other.example"
				}
				port := conn.LocalAddr().(*net.UDPAddr).Port
				out := probe(ctx, request{Address: "127.0.0.1", Port: port, SNI: sni, ObfsPassword: obfs}, roots)
				want := policy
				if policy == "wrong-name" || policy == "expired" {
					want = "invalid"
				}
				if out.Status != want {
					t.Fatalf("want %s, got %s", want, out.Status)
				}
				hash := sha256.Sum256(cert.Certificate[0])
				if out.Pin != hex.EncodeToString(hash[:]) {
					t.Fatal("wrong certificate hash")
				}
			})
		}
	}
}

func TestProbeTimeoutAndInvalidInput(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if probe(ctx, request{}, nil).Status != "error" {
		t.Fatal("invalid input accepted")
	}
	if probe(ctx, request{Address: "127.0.0.1", Port: 1, SNI: "probe.example"}, nil).Status != "error" {
		t.Fatal("offline peer accepted")
	}
}
