# Tinfoil Containers, as used in this repo

This repo is a complete, small Tinfoil Container. If you are coming from Google Confidential
Space, this page maps the concepts and shows where each one lives here. The full product
docs are at [docs.tinfoil.sh/containers](https://docs.tinfoil.sh/containers/overview).

## The idea in one paragraph

A Tinfoil Container is an ordinary Docker image running inside a confidential VM (AMD SEV-SNP or
Intel TDX, with NVIDIA confidential computing on the GPUs) on hardware Tinfoil operates. You do
not build the VM. You write one file, `tinfoil-config.yml`, in a public GitHub repo: which image
digest to run, its environment, command, ports, egress rules, model weights, and how much
CPU/memory/GPU it gets. A GitHub Actions release measures that file together with Tinfoil's VM
image and publishes the expected enclave measurement to Sigstore. A client verifies a running
enclave by fetching its hardware attestation and checking it against that published
measurement, on the client's own machine, against AMD's, Intel's and NVIDIA's certificate roots.
Tinfoil is not in the trust path.

## Coming from Confidential Space

| Confidential Space | Tinfoil Containers | In this repo |
|---|---|---|
| Confidential Space VM image (`confidential-space` image family) | `cvmimage`, Tinfoil's measured VM image, pinned by `cvm-version` | `cvm-version: 0.14.7` in `tinfoil-config.yml` |
| Workload container set by `tee-image-reference` metadata, plus launch policies | `tinfoil-config.yml`: image digest, env, command, ports, egress, resources. The whole file is measured | `containers:` block; the image is pinned by digest at release time |
| Attestation token: an OIDC JWT signed by Google, checked against Google's JWKs | Attestation document: the raw SEV-SNP or TDX quote plus GPU evidence, checked against vendor roots | `dbe verify` runs `tinfoil attestation verify`; the document is at `/.well-known/tinfoil-attestation` |
| Image digest as a claim inside Google's token | Enclave measurement published to Sigstore by the release workflow, compared with the live quote | `.github/workflows/tinfoil-release*.yml`; the release's `tinfoil.hash` |
| `gcloud compute instances create` / Terraform | `tinfoil container create NAME --repo OWNER/REPO --tag vX.Y.Z` (or the dashboard) | README, "Deploy your own" |
| Workload Identity Federation and KMS key release on attestation claims | Tinfoil Secrets: your own key server releases secrets to enclaves that present a valid attestation for a pinned repo, tag and domain | Not used yet; planned for encrypted weights at rest ([docs](https://docs.tinfoil.sh/containers/private-secrets)) |
| No inbound ports; peers reach the workload through an outbound channel | A public HTTPS endpoint per container, `NAME.ORG.containers.tinfoil.dev`, terminated by a small in-enclave proxy (the shim) that forwards only the paths you list | `shim.paths: ["/api/*"]`; vLLM stays on loopback |
| Egress allowed by default | Egress closed unless a `networks:` block declares an allowlist | No `networks:` block here, so the enclave cannot call out |
| Debug via `tee-container-log-redirect` or a debug image | `--debug --ssh-key` launch with SSH. It changes the measurement, so verifiers reject it; use it only while developing | Not used by parties; the operator used it once to read container logs during bring-up |
| Persistent disks | Encrypted volumes (dm-crypt + dm-integrity), unlocked at boot from a secret or at runtime | Not used; assets live in tmpfs and die with the enclave |
| a3 VMs with H100 in CC mode | H200, B200 and B300 hosts with GPU attestation in the same document as the CPU quote | `gpus: 1`, `runtime: nvidia` |

## Reading tinfoil-config.yml

The file is the whole deployment. Top to bottom:

- `cvm-version`: which measured VM image boots. Changing it changes the measurement.
- `cpus`, `memory`, `gpus`: the VM shape. GPU counts are 1 or 8.
- `models:`: weights delivered as verified read-only disks, named by Hugging Face repo and
  revision plus a root hash (`mpk`). The enclave mounts them at `/tinfoil/mpk/mpk-<roothash>`;
  the root hash is the model's identity in receipts. Encrypted variants (`emwp` + `key-secret`)
  keep weights private from the operator.
- `containers:`: the Docker containers to run. `image` must carry a digest. `env` and `command`
  are measured, which is how the party public keys and the output policy become part of what
  everyone verifies. The root filesystem is read-only; `tmpfs` gives scratch space.
- `healthcheck`: Docker's health check, which the VM waits on before declaring the enclave ready.
- `shim`: which container and port face the internet, and which paths are reachable. Everything
  else is 404.
- `networks:` (absent here): the only way to get egress, as a per-container allowlist.

## The release and deploy loop

1. Change the config or the code, merge to `main`.
2. Run the **Tinfoil Release** workflow with a version. It builds and pushes the image, pins its
   digest into `tinfoil-config.yml` in a release commit, tags it, and dispatches the publish
   workflow, which measures the config plus VM image and publishes the measurement and a Sigstore
   attestation on the GitHub release.
3. `tinfoil container create` (or update) deploys that tag. The enclave boots, attests, gets a
   TLS certificate for its domain, pulls the image, waits for the health check, and opens the shim.
4. Every party runs `dbe verify` (any client using Tinfoil's verifier) against the new release.

Every release is a new measurement, so a redeploy always means re-verification. That is a
feature: nothing can change under the parties without both noticing.

## Things that are easy to miss

- The config repo has to be public; the image can be private (pulled with registry credentials).
- Anyone can redeploy your exact repo and tag and get an identical, valid measurement. That is
  why party identity lives inside the measured config here, and why Tinfoil Secrets pins the
  deployment domain as well as the repo and tag.
- The operator sees ciphertext sizes and timing, and can stop or delete the enclave. It cannot
  read memory, disks in the VM, or traffic inside the attested TLS session.
- The `tinfoil` CLI and the Python, Go and JavaScript SDKs all verify the same way; the SDKs pin
  the TLS connection to the attested key so an application call cannot land anywhere else.
