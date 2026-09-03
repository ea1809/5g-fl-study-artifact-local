# Multi-Edge 5G Federated Anomaly Detection Artifact — Open5GS v2.7.7

This repository contains the deployment, traffic generation, attack orchestration, packet capture, and dataset extraction components used for a Kubernetes-based multi-edge 5G testbed.

The current version uses **Open5GS v2.7.7**, **Kubernetes v1.29.15**, and **UERANSIM v3.2.6**.

The platform generates synchronized N3 and N4 observations around multiple UPFs and exposes different edge sites to different attack classes. The resulting datasets can be used to study distributed and federated intrusion detection, including zero-local-exposure scenarios.

---

# Architecture

The testbed contains three virtual machines:

| Vagrant machine         | Kubernetes node     | IP             | Role                     |
| ----------------------- | ------------------- | -------------- | ------------------------ |
| `master-open5gs-277`    | `master-open5gs`    | `192.168.57.5` | Kubernetes control plane |
| `worker-open5gs-277-01` | `worker-open5gs-01` | `192.168.57.6` | Cloud / Edge 1           |
| `worker-open5gs-277-02` | `worker-open5gs-02` | `192.168.57.7` | Edge 2                   |

The Vagrant machine names and Kubernetes node names are intentionally different.

Worker roles are:

```text
worker-open5gs-01 -> edge-role=cloud
worker-open5gs-02 -> edge-role=edge
```

The deployment contains:

```text
                    Shared 5G Core
             AMF / NRF / SCP / AUSF / UDM
                         |
          +--------------+--------------+
          |                             |
       Edge 1                         Edge 2
      SMF2/UPF2                      SMF3/UPF3
          |                             |
       N3 + N4                        N3 + N4
          |                             |
     Edge 1 UEs                    Edge 2 UEs
```

A cloud SMF/UPF pair is also deployed for the `internet` DNN.

---

# Repository structure

```text
.
├── inventory/
│   └── vagrant.hosts
├── playbooks/
│   ├── ansible-playbook.yaml
│   ├── open5gs-playbook.yaml
│   ├── 5g-edge-playbook.yaml
│   └── files/
│       └── 5g-static/
│           └── open5gs-core/
├── scripts/
│   └── extract_global_windows.py
├── vm-files/
│   ├── attacks/
│   │   ├── pfcp/
│   │   ├── pfcp-edge2/
│   │   ├── gtp/
│   │   └── gtp-edge2/
│   ├── http-services/
│   ├── scenarios/
│   ├── attacker-pod.yaml
│   ├── network-attachments-all-ns.yaml
│   └── orchestrator.sh
├── Vagrantfile
├── .gitignore
└── README.md
```

Large generated or binary files are intentionally not tracked by Git.

In particular:

```text
output/
ueransim-images-v3.2.6.tar
*.pcap
*.pcapng
```

The UERANSIM image archive must therefore be obtained separately before running the image import step.

The `old_ues/` directory contains legacy manifests and must **not** be deployed.

The active UE manifests are:

```text
ue-lifecycle-cloud-deployment.yaml
ue-lifecycle-edge1-deployment.yaml
ue-lifecycle-edge2-deployment.yaml
```

---

# 1. Requirements

Validated environment:

```text
Host OS             Ubuntu 24.04
Guest OS            Ubuntu 20.04
Vagrant             2.4.9
Kubernetes          1.29.15
Open5GS             2.7.7
UERANSIM            3.2.6
Helm                3.x
Container runtime   containerd
CNI                 Calico + Multus + OVS-CNI
```

Required host packages include:

```bash
sudo apt update

sudo apt install -y \
  qemu-kvm \
  libvirt-daemon-system \
  libvirt-clients \
  bridge-utils \
  ansible
```

Verify Vagrant:

```bash
vagrant --version
```

Install the libvirt plugin if required:

```bash
vagrant plugin install vagrant-libvirt
```

Verify:

```bash
vagrant plugin list
```

---

# 2. Open5GS v2.7.7

If the Open5GS source archive is required locally, download v2.7.7:

```bash
wget \
  https://github.com/open5gs/open5gs/archive/refs/tags/v2.7.7.tar.gz \
  -O open5gs-2.7.7.tar.gz
```

Extract it:

```bash
tar -xzf open5gs-2.7.7.tar.gz
```

Verify:

```bash
ls open5gs-2.7.7
```

The Kubernetes deployment uses Open5GS **2.7.7** container images.

The Helm deployment is configured with:

```yaml
open5gs_chart_ref: oci://registry-1.docker.io/gradiantcharts/open5gs
open5gs_chart_version: "2.3.4"
```

Open5GS NF images must use:

```text
docker.io/gradiant/open5gs:2.7.7
```

The WebUI uses:

```text
docker.io/gradiant/open5gs-webui:2.7.7
```

MongoDB and `open5gs-dbctl` use their own images and therefore do not use the Open5GS `2.7.7` tag.

---

# 3. UERANSIM images

UERANSIM v3.2.6 is used.

The custom images are supplied separately as:

```text
ueransim-images-v3.2.6.tar
```

The archive is not stored in this Git repository because of its size.

Before continuing with the UERANSIM image import step, place the archive in the repository root.

The archive contains:

```text
docker.io/library/ueransim-iperf3:v3.2.6
docker.io/library/ueransim-tools:v3.2.6
```

The deployments use:

```text
Cloud UE:
  ueransim-iperf3:v3.2.6

Edge 1 UE:
  ueransim-tools:v3.2.6

Edge 2 UE:
  ueransim-tools:v3.2.6

gNBs:
  ghcr.io/niloysh/ueransim:v3.2.6
```

`ueransim-tools:v3.2.6` is required on the edge UEs because the traffic generator uses tools such as `curl`.

---

# 4. Start the virtual machines

From the repository root on the **host**:

```bash
vagrant up
```

Verify:

```bash
vagrant status
```

Expected Vagrant machines:

```text
master-open5gs-277
worker-open5gs-277-01
worker-open5gs-277-02
```

The corresponding guest/Kubernetes hostnames are:

```text
master-open5gs
worker-open5gs-01
worker-open5gs-02
```

---

# 5. Verify VM connectivity

From the host, verify that each VM is accessible through Vagrant.

Master:

```bash
vagrant ssh master-open5gs-277
```

Worker 1:

```bash
vagrant ssh worker-open5gs-277-01
```

Worker 2:

```bash
vagrant ssh worker-open5gs-277-02
```

Exit each VM with:

```bash
exit
```

---

# 6. Copy the experiment files to the master

From the **host**, at the repository root, remove any previous copy before transferring the current artifact files.

This avoids keeping stale or duplicated files from an earlier deployment:

```bash
ssh \
  -i .vagrant/machines/master-open5gs-277/libvirt/private_key \
  vagrant@192.168.57.5 \
  "rm -rf /home/vagrant/vm-files"
```

Copy the current `vm-files` directory:

```bash
scp -r \
  -i .vagrant/machines/master-open5gs-277/libvirt/private_key \
  vm-files \
  vagrant@192.168.57.5:/home/vagrant/
```

Verify on the master:

```bash
vagrant ssh master-open5gs-277
```

Then:

```bash
find ~/vm-files -maxdepth 2 -type f
```

At minimum, the following resources must be available:

```text
~/vm-files/
├── attacks/
├── http-services/
├── scenarios/
├── attacker-pod.yaml
├── network-attachments-all-ns.yaml
└── orchestrator.sh
```

---

# 7. Create the Kubernetes cluster

From the **host**:

```bash
ansible-playbook \
  -i inventory/vagrant.hosts \
  playbooks/ansible-playbook.yaml
```

The playbook initializes the master and creates a fresh Kubernetes worker join command.

Verify from the master:

```bash
kubectl get nodes -o wide
```

Expected:

```text
master-open5gs      Ready
worker-open5gs-01   Ready
worker-open5gs-02   Ready
```

All nodes should run Kubernetes v1.29.15.

---

# 8. Assign worker roles

From the **master**:

```bash
kubectl label node \
  worker-open5gs-01 \
  edge-role=cloud \
  --overwrite
```

```bash
kubectl label node \
  worker-open5gs-02 \
  edge-role=edge \
  --overwrite
```

Verify:

```bash
kubectl get nodes --show-labels
```

These labels are required by the SMF/UPF `nodeSelector` configuration.

---

# 9. Install Multus

From the master:

```bash
kubectl apply -f \
  https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset-thick.yml
```

Verify:

```bash
kubectl get pods \
  -n kube-system \
  -o wide \
  | grep multus
```

One Multus pod should run on each Kubernetes node.

Increase the Multus resource limits:

```bash
kubectl set resources daemonset kube-multus-ds \
  -n kube-system \
  -c kube-multus \
  --requests=cpu=100m,memory=64Mi \
  --limits=cpu=500m,memory=256Mi
```

Wait for completion:

```bash
kubectl rollout status \
  daemonset/kube-multus-ds \
  -n kube-system
```

---

# 10. Install Open vSwitch

Run on **both workers**.

Install OVS:

```bash
sudo apt update

sudo apt install -y \
  openvswitch-switch
```

Start it:

```bash
sudo systemctl enable --now openvswitch-switch
```

Verify:

```bash
sudo systemctl status \
  openvswitch-switch \
  --no-pager
```

---

# 11. Create the OVS bridges

Run on both workers:

```bash
sudo ovs-vsctl --may-exist add-br n2br
sudo ovs-vsctl --may-exist add-br n3br
sudo ovs-vsctl --may-exist add-br n4br
```

Verify:

```bash
sudo ovs-vsctl show
```

Expected bridges:

```text
n2br
n3br
n4br
```

N2 remains local to the worker.

N3 and N4 are interconnected between workers using VXLAN.

---

# 12. Configure the VXLAN tunnels

## Important

Calico already uses UDP port **4789** for its VXLAN overlay.

The OVS tunnels therefore use UDP port:

```text
8472
```

Do **not** configure the OVS VXLAN tunnels on UDP 4789.

## Worker 1

On `worker-open5gs-01`:

```bash
sudo ovs-vsctl --may-exist add-port n3br vxlan-n3-w2 \
  -- set interface vxlan-n3-w2 \
  type=vxlan \
  options:remote_ip=192.168.57.7 \
  options:key=103 \
  options:dst_port=8472
```

```bash
sudo ovs-vsctl --may-exist add-port n4br vxlan-n4-w2 \
  -- set interface vxlan-n4-w2 \
  type=vxlan \
  options:remote_ip=192.168.57.7 \
  options:key=104 \
  options:dst_port=8472
```

## Worker 2

On `worker-open5gs-02`:

```bash
sudo ovs-vsctl --may-exist add-port n3br vxlan-n3-w1 \
  -- set interface vxlan-n3-w1 \
  type=vxlan \
  options:remote_ip=192.168.57.6 \
  options:key=103 \
  options:dst_port=8472
```

```bash
sudo ovs-vsctl --may-exist add-port n4br vxlan-n4-w1 \
  -- set interface vxlan-n4-w1 \
  type=vxlan \
  options:remote_ip=192.168.57.6 \
  options:key=104 \
  options:dst_port=8472
```

Verify on both workers:

```bash
sudo ovs-vsctl show
```

---

# 13. Install OVS-CNI

From the master:

```bash
cd ~

git clone \
  https://github.com/k8snetworkplumbingwg/ovs-cni.git

cd ovs-cni
```

Deploy OVS-CNI:

```bash
kubectl apply -f examples/ovs-cni.yml
```

Verify:

```bash
kubectl get pods \
  -n kube-system \
  -o wide \
  | grep ovs-cni
```

One OVS-CNI pod should run on each worker.

Verify that the OVS CNI binary has actually been installed on both workers.

On Worker 1:

```bash
sudo test -x /opt/cni/bin/ovs && echo "OVS CNI OK"
```

On Worker 2:

```bash
sudo test -x /opt/cni/bin/ovs && echo "OVS CNI OK"
```

Do not deploy Open5GS until this check succeeds.

---

# 14. Deploy the secondary N2/N3/N4 networks

Ensure that the namespaces exist:

```bash
for ns in open5gs-core open5gs-cloud open5gs-edge; do
  kubectl create namespace "$ns" \
    --dry-run=client \
    -o yaml \
    | kubectl apply -f -
done
```

Deploy the NetworkAttachmentDefinitions:

```bash
kubectl apply -f \
  ~/vm-files/network-attachments-all-ns.yaml
```

Verify:

```bash
kubectl get \
  network-attachment-definitions \
  -A
```

The deployment uses:

```text
n2network
n3network
n4network
```

---

# 15. Import UERANSIM images

The custom UERANSIM images must be imported into **both workers**.

First, verify from the host that the separately provided archive is available in the repository root:

```bash
ls -lh ueransim-images-v3.2.6.tar
```

Do not continue until this file is available.

From the host, copy the archive to Worker 1:

```bash
scp \
  -i .vagrant/machines/worker-open5gs-277-01/libvirt/private_key \
  ueransim-images-v3.2.6.tar \
  vagrant@192.168.57.6:/tmp/
```

Copy it to Worker 2:

```bash
scp \
  -i .vagrant/machines/worker-open5gs-277-02/libvirt/private_key \
  ueransim-images-v3.2.6.tar \
  vagrant@192.168.57.7:/tmp/
```

On Worker 1:

```bash
sudo ctr \
  -n k8s.io \
  images import \
  /tmp/ueransim-images-v3.2.6.tar
```

On Worker 2:

```bash
sudo ctr \
  -n k8s.io \
  images import \
  /tmp/ueransim-images-v3.2.6.tar
```

Verify on both workers:

```bash
sudo ctr \
  -n k8s.io \
  images list \
  | grep ueransim
```

The following local images must exist:

```text
docker.io/library/ueransim-iperf3:v3.2.6
docker.io/library/ueransim-tools:v3.2.6
```

The gNB image is:

```text
ghcr.io/niloysh/ueransim:v3.2.6
```

---

# 16. Deploy Open5GS and edge resources

From the **host**, deploy Open5GS:

```bash
ansible-playbook \
  -i inventory/vagrant.hosts \
  playbooks/open5gs-playbook.yaml
```

Then deploy the edge and UERANSIM resources:

```bash
ansible-playbook \
  -i inventory/vagrant.hosts \
  playbooks/5g-edge-playbook.yaml
```

Monitor the pods from the master:

```bash
kubectl get pods -A -w
```

Display problematic pods:

```bash
kubectl get pods -A \
  | grep -E \
  'Init:|PodInitializing|ContainerCreating|CrashLoopBackOff|ImagePullBackOff|ErrImagePull|Error'
```

Verify Open5GS images:

```bash
kubectl get pods -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{range .spec.containers[*]}{.image}{" "}{end}{"\n"}{end}' \
  | grep -E 'open5gs|gradiant'
```

Open5GS network functions should use version:

```text
2.7.7
```

---

# 17. Register Open5GS subscribers

The experiment uses **50 subscribers**.

The distribution is:

| IMSI  | DNN        | Site   |
| ----- | ---------- | ------ |
| 1–10  | `internet` | Cloud  |
| 11–30 | `edge1`    | Edge 1 |
| 31–50 | `edge2`    | Edge 2 |

Retrieve the MongoDB pod:

```bash
MONGOPOD=$(kubectl get pods \
  -n open5gs-core \
  | grep mongodb \
  | awk '{print $1}')
```

Before inserting the subscribers, check the current database:

```bash
kubectl exec \
  -n open5gs-core \
  "$MONGOPOD" \
  -- mongosh open5gs \
  --quiet \
  --eval 'db.subscribers.countDocuments()'
```

If an old test population is already present, remove it before recreating the validated 50-subscriber population:

```bash
kubectl exec \
  -n open5gs-core \
  "$MONGOPOD" \
  -- mongosh open5gs \
  --quiet \
  --eval 'db.subscribers.deleteMany({})'
```

Insert the 50 subscribers:

```bash
kubectl exec -n open5gs-core "$MONGOPOD" -- mongosh open5gs --eval '
for (let i = 11; i <= 30; i++) {
  let imsi = "99970000000" + String(i).padStart(4, "0");
  db.subscribers.insertOne({
    imsi: imsi,
    msisdn: [],
    imeisv: "4370816125816161",
    mme_host: [],
    mme_realm: [],
    purge_flag: [],
    security: {
      k: "465B5CE8B199B49FAA5F0A2EE238A6BC",
      op: null,
      opc: "E8ED289DEBA952E4283B54E88E6183CA",
      amf: "8000"
    },
    ambr: {
      downlink: { value: 1, unit: 3 },
      uplink: { value: 1, unit: 3 }
    },
    slice: [{
      sst: 1,
      sd: "000002",
      default_indicator: true,
      session: [{
        name: "edge1",
        type: 3,
        ambr: {
          downlink: { value: 1, unit: 3 },
          uplink: { value: 1, unit: 3 }
        },
        qos: {
          index: 9,
          arp: {
            priority_level: 8,
            pre_emption_capability: 1,
            pre_emption_vulnerability: 1
          }
        }
      }]
    }],
    access_restriction_data: 32,
    subscriber_status: 0,
    network_access_mode: 0,
    subscribed_rau_tau_timer: 12
  });
}

for (let i = 31; i <= 50; i++) {
  let imsi = "99970000000" + String(i).padStart(4, "0");
  db.subscribers.insertOne({
    imsi: imsi,
    msisdn: [],
    imeisv: "4370816125816181",
    mme_host: [],
    mme_realm: [],
    purge_flag: [],
    security: {
      k: "465B5CE8B199B49FAA5F0A2EE238A6BC",
      op: null,
      opc: "E8ED289DEBA952E4283B54E88E6183CA",
      amf: "8000"
    },
    ambr: {
      downlink: { value: 1, unit: 3 },
      uplink: { value: 1, unit: 3 }
    },
    slice: [{
      sst: 1,
      sd: "000002",
      default_indicator: true,
      session: [{
        name: "edge2",
        type: 3,
        ambr: {
          downlink: { value: 1, unit: 3 },
          uplink: { value: 1, unit: 3 }
        },
        qos: {
          index: 9,
          arp: {
            priority_level: 8,
            pre_emption_capability: 1,
            pre_emption_vulnerability: 1
          }
        }
      }]
    }],
    access_restriction_data: 32,
    subscriber_status: 0,
    network_access_mode: 0,
    subscribed_rau_tau_timer: 12
  });
}

for (let i = 1; i <= 10; i++) {
  let imsi = "99970000000" + String(i).padStart(4, "0");
  db.subscribers.insertOne({
    imsi: imsi,
    msisdn: [],
    imeisv: "4370816125816151",
    mme_host: [],
    mme_realm: [],
    purge_flag: [],
    security: {
      k: "465B5CE8B199B49FAA5F0A2EE238A6BC",
      op: null,
      opc: "E8ED289DEBA952E4283B54E88E6183CA",
      amf: "8000"
    },
    ambr: {
      downlink: { value: 1, unit: 3 },
      uplink: { value: 1, unit: 3 }
    },
    slice: [{
      sst: 1,
      sd: "000001",
      default_indicator: true,
      session: [{
        name: "internet",
        type: 3,
        ambr: {
          downlink: { value: 1, unit: 3 },
          uplink: { value: 1, unit: 3 }
        },
        qos: {
          index: 9,
          arp: {
            priority_level: 8,
            pre_emption_capability: 1,
            pre_emption_vulnerability: 1
          }
        }
      }]
    }],
    access_restriction_data: 32,
    subscriber_status: 0,
    network_access_mode: 0,
    subscribed_rau_tau_timer: 12
  });
}

print("Total: " + db.subscribers.countDocuments());
'
```

The active configuration is:

```text
IMSI 1-10:
  S-NSSAI = SST 1 / SD 000001
  DNN     = internet

IMSI 11-30:
  S-NSSAI = SST 1 / SD 000002
  DNN     = edge1

IMSI 31-50:
  S-NSSAI = SST 1 / SD 000002
  DNN     = edge2
```

Verify the final subscriber count:

```bash
kubectl exec \
  -n open5gs-core \
  "$MONGOPOD" \
  -- mongosh open5gs \
  --quiet \
  --eval 'db.subscribers.countDocuments()'
```

Expected:

```text
50
```

---

# 18. Restart the gNB and UE deployments

Stop the UEs:

```bash
kubectl scale deployment \
  ueransim-ue-lifecycle-edge1 \
  ueransim-ue-lifecycle-edge2 \
  ueransim-ue-lifecycle-cloud \
  --replicas=0 \
  -n open5gs-core
```

Wait:

```bash
sleep 10
```

Restart the gNBs:

```bash
kubectl rollout restart deployment \
  ueransim-gnb-edge1 \
  ueransim-gnb-edge2 \
  -n open5gs-core
```

Wait:

```bash
sleep 20
```

Check NG Setup:

```bash
kubectl logs \
  -n open5gs-core \
  deployment/ueransim-gnb-edge1 \
  --tail=20 \
  | grep -i "NG Setup"
```

Repeat for Edge 2 if required:

```bash
kubectl logs \
  -n open5gs-core \
  deployment/ueransim-gnb-edge2 \
  --tail=20 \
  | grep -i "NG Setup"
```

Restart the UEs:

```bash
kubectl scale deployment \
  ueransim-ue-lifecycle-edge1 \
  ueransim-ue-lifecycle-edge2 \
  ueransim-ue-lifecycle-cloud \
  --replicas=1 \
  -n open5gs-core
```

Allow enough time for all registrations and PDU sessions:

```bash
sleep 120
```

---

# 19. Verify UE connectivity

Retrieve the Edge 1 lifecycle pod:

```bash
UEPOD=$(kubectl get pods \
  -n open5gs-core \
  -l component=ue-lifecycle,edge=edge1 \
  -o jsonpath='{.items[0].metadata.name}')
```

Check the UE tunnel interfaces:

```bash
kubectl exec \
  -n open5gs-core \
  "$UEPOD" \
  -- ip -o -4 addr show \
  | grep uesim
```

Edge 1 is configured with 20 UEs, therefore a healthy deployment should expose approximately:

```text
uesimtun0
uesimtun1
...
uesimtun19
```

The same check can be performed for Edge 2.

---

# 20. Configure HTTP traffic services

Run the HTTP service installation commands on the **master**.

Three HTTP services are hosted on the master:

```text
web_light  -> 192.168.57.5:8081
web_heavy  -> 192.168.57.5:8082
video      -> 192.168.57.5:8083
```

Install the systemd units:

```bash
sudo cp \
  ~/vm-files/http-services/ue-http-web-light.service \
  /etc/systemd/system/
```

```bash
sudo cp \
  ~/vm-files/http-services/ue-http-web-heavy.service \
  /etc/systemd/system/
```

```bash
sudo cp \
  ~/vm-files/http-services/ue-http-video.service \
  /etc/systemd/system/
```

Create the HTTP directories:

```bash
sudo mkdir -p \
  /opt/ue-http-small \
  /opt/ue-http-medium \
  /opt/ue-http-video
```

Generate test files:

```bash
sudo dd \
  if=/dev/urandom \
  of=/opt/ue-http-small/small.bin \
  bs=1K \
  count=64
```

```bash
sudo dd \
  if=/dev/urandom \
  of=/opt/ue-http-medium/medium.bin \
  bs=1M \
  count=2
```

```bash
sudo dd \
  if=/dev/urandom \
  of=/opt/ue-http-video/video.bin \
  bs=1M \
  count=20
```

Enable the services:

```bash
sudo systemctl daemon-reload

sudo systemctl enable --now \
  ue-http-web-light.service \
  ue-http-web-heavy.service \
  ue-http-video.service
```

Verify:

```bash
ss -lntp \
  | grep -E '8081|8082|8083'
```

Test locally:

```bash
curl -s \
  -o /dev/null \
  -w "code=%{http_code} size=%{size_download}\n" \
  http://192.168.57.5:8081/small.bin
```

Expected HTTP status:

```text
code=200
```

## UE configuration

The edge lifecycle ConfigMaps must use:

```bash
SRV_LIGHT="192.168.57.5:8081"
SRV_HEAVY="192.168.57.5:8082"
SRV_VIDEO="192.168.57.5:8083"
```

From the **host**, at the repository root, verify the UE traffic configuration:

```bash
grep -R \
  'SRV_LIGHT\|SRV_HEAVY\|SRV_VIDEO' \
  playbooks/files/5g-static/open5gs-core/ue-lifecycle-edge*-configmap.yaml
```

From the **master**, verify UE-generated HTTP traffic:

```bash
kubectl exec \
  -n open5gs-core \
  "$UEPOD" \
  -- grep "code=200" \
  /tmp/ue-traffic.log \
  | tail -3
```

Example:

```text
[TRAFFIC] ... tun=uesimtun0 profile=web_heavy code=200 ...
```

---

# 21. Deploy the attacker pod

From the master:

```bash
kubectl get pod \
  pfcp-attacker \
  -n open5gs-edge
```

If it does not exist:

```bash
kubectl apply -f \
  ~/vm-files/attacker-pod.yaml
```

Verify:

```bash
kubectl get pod \
  pfcp-attacker \
  -n open5gs-edge \
  -o wide
```

The attacker pod is connected to the N3/N4 experimental networks and is used by the orchestration scripts.

---

# 22. Install attacker dependencies

The attacker uses:

```text
Python
Scapy
Faker
hping3
iperf3
```

If direct Internet access from the attacker pod is unavailable, download packages on the master and copy them into the pod.

## Scapy

On the master:

```bash
mkdir -p /tmp/scapy_pkg

pip3 download \
  scapy==2.7.0 \
  --no-deps \
  -d /tmp/scapy_pkg/
```

Copy:

```bash
kubectl cp \
  /tmp/scapy_pkg/ \
  pfcp-attacker:/tmp/scapy_pkg \
  -n open5gs-edge
```

Install:

```bash
kubectl exec \
  -it pfcp-attacker \
  -n open5gs-edge \
  -- pip3 install \
  --no-index \
  --find-links=/tmp/scapy_pkg \
  scapy==2.7.0
```

## Faker

```bash
mkdir -p /tmp/faker_pkg

pip3 download \
  faker \
  -d /tmp/faker_pkg/
```

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- mkdir -p /tmp/faker_pkg
```

```bash
for f in /tmp/faker_pkg/*.whl; do
  kubectl cp \
    "$f" \
    pfcp-attacker:/tmp/faker_pkg/ \
    -n open5gs-edge
done
```

Install:

```bash
kubectl exec \
  -it pfcp-attacker \
  -n open5gs-edge \
  -- pip3 install \
  --no-index \
  --find-links=/tmp/faker_pkg \
  faker
```

Verify:

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- python3 -c \
  "import scapy, faker; print('OK')"
```

## hping3 and iperf3

On the master:

```bash
cd /tmp

apt-get download \
  hping3 \
  iperf3 \
  libiperf0 \
  libpcap0.8 \
  libsctp1 \
  libssl1.1 \
  2>/dev/null || true
```

Copy the packages:

```bash
for f in \
  /tmp/hping3*.deb \
  /tmp/iperf3*.deb \
  /tmp/libiperf0*.deb \
  /tmp/libpcap0.8*.deb \
  /tmp/libsctp1*.deb \
  /tmp/libssl1.1*.deb
do
  [[ -f "$f" ]] && \
    kubectl cp \
      "$f" \
      pfcp-attacker:/tmp/ \
      -n open5gs-edge
done
```

Install:

```bash
kubectl exec \
  -it pfcp-attacker \
  -n open5gs-edge \
  -- dpkg -i /tmp/*.deb \
  2>/dev/null || true
```

Verify:

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- which hping3
```

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- which iperf3
```

---

# 23. Install master capture dependencies

On the master:

```bash
sudo apt update

sudo apt install -y \
  tshark \
  python3-pip
```

Install Scapy:

```bash
pip3 install scapy==2.7.0
```

Verify:

```bash
tshark --version
```

```bash
python3 -c \
  "import scapy; print(scapy.__version__)"
```

Expected:

```text
2.7.0
```

---

# 24. Prepare the experiment orchestrator

On the master:

```bash
mkdir -p ~/orchestrator

rm -rf \
  ~/orchestrator/scenarios \
  ~/orchestrator/attacks
```

Removing the previous `scenarios/` and `attacks/` directories prevents obsolete or duplicated scripts from an earlier deployment from remaining in the orchestrator.

Copy the orchestrator:

```bash
cp \
  ~/vm-files/orchestrator.sh \
  ~/orchestrator/
```

Copy scenarios:

```bash
cp -r \
  ~/vm-files/scenarios \
  ~/orchestrator/
```

Copy attacks:

```bash
cp -r \
  ~/vm-files/attacks \
  ~/orchestrator/
```

Set permissions:

```bash
chmod +x \
  ~/orchestrator/orchestrator.sh
```

```bash
find \
  ~/orchestrator/attacks \
  -type f \
  -name "*.sh" \
  -exec chmod +x {} \;
```

Expected structure:

```text
~/orchestrator/
├── orchestrator.sh
├── scenarios/
│   ├── fl_edge1.conf
│   ├── fl_edge2.conf
│   └── ...
└── attacks/
    ├── pfcp/
    ├── pfcp-edge2/
    ├── gtp/
    └── gtp-edge2/
```

## Important path handling

Attack script paths must be resolved relative to `orchestrator.sh`.

The script must not use paths such as:

```text
~/attacks/pfcp
~/attacks/gtp
```

Instead it uses its own directory, e.g.:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

and paths below:

```text
$SCRIPT_DIR/attacks/
```

This prevents errors such as:

```text
WARN: impossible de copier pfcp-flood-upf2.py
```

---

# 25. Experiment scenarios

## Edge 1

Edge 1 contains the following attack classes:

```text
pfcp_flood
pfcp_session_del
gtp_flood
udp_flood_low
udp_flood_medium
```

It also contains benign background and intensive HTTP traffic.

Scenario:

```text
0 5 normal
5 7 pfcp_flood
7 10 normal
10 12 pfcp_session_del
12 15 normal
15 17 gtp_flood
17 20 normal
20 22 udp_flood_low
22 24 udp_flood_medium
24 26 normal
26 28 ue_http_intensive
28 31 normal
31 40 normal
```

## Edge 2

Edge 2 contains:

```text
pfcp_flood
pfcp_session_mod
udp_flood_high
udp_flood_max
```

Scenario:

```text
0 5 normal
5 7 pfcp_flood
7 10 normal
10 12 pfcp_session_mod
12 15 normal
15 17 udp_flood_high
17 19 udp_flood_max
19 22 normal
22 24 ue_http_intensive
24 27 normal
27 40 normal
```

The two edges therefore have deliberately different attack distributions.

This creates the asymmetric local class exposure required by the federated-learning experiment.

---

# 26. Run the experiments

All experiment runs are started from the **master**.

Remove an old run only when a completely new capture is required:

```bash
rm -rf \
  ~/orchestrator/output/run_fl_edge1 \
  ~/orchestrator/output/run_fl_edge2
```

## Edge 1

```bash
~/orchestrator/orchestrator.sh \
  ~/orchestrator/scenarios/fl_edge1.conf \
  run_fl_edge1 \
  edge1
```

## Edge 2

```bash
~/orchestrator/orchestrator.sh \
  ~/orchestrator/scenarios/fl_edge2.conf \
  run_fl_edge2 \
  edge2
```

General syntax:

```text
orchestrator.sh <scenario_file> <run_id> <edge>
```

Outputs are written to:

```text
~/orchestrator/output/<run_id>/
```

Example:

```text
run_fl_edge1/
├── events.csv
├── logs/
│   ├── orchestrator.log
│   ├── capture_n3.log
│   └── capture_n4.log
└── pcaps/
    ├── run_fl_edge1_n3.pcap
    └── run_fl_edge1_n4.pcap
```

The N3 capture uses a reduced packet snapshot length to limit storage usage.

---

# 27. Validate a completed run

Check that the recorded experiment events match the scenario:

```bash
cat \
  ~/orchestrator/output/run_fl_edge1/events.csv
```

Compare with:

```bash
cat \
  ~/orchestrator/scenarios/fl_edge1.conf
```

Check the capture times:

```bash
capinfos \
  -a -e -d \
  ~/orchestrator/output/run_fl_edge1/pcaps/run_fl_edge1_n3.pcap
```

```bash
capinfos \
  -a -e -d \
  ~/orchestrator/output/run_fl_edge1/pcaps/run_fl_edge1_n4.pcap
```

A complete 40-minute scenario should have approximately 40 minutes of capture coverage.

Inspect N3 traffic:

```bash
tshark \
  -r ~/orchestrator/output/run_fl_edge1/pcaps/run_fl_edge1_n3.pcap \
  -c 20
```

Inspect N4 traffic:

```bash
tshark \
  -r ~/orchestrator/output/run_fl_edge1/pcaps/run_fl_edge1_n4.pcap \
  -c 20
```

N4 should contain PFCP activity.

N3 should contain GTP-U encapsulated UE traffic and the configured attack traffic.

---

# 28. Copy experiment results to the host

Run these commands from the **host**, from the repository root.

The `output/` directory contains generated experiment results and is not tracked by Git.

Create the output directory:

```bash
mkdir -p output
```

## Edge 1

```bash
scp -r \
  -i .vagrant/machines/master-open5gs-277/libvirt/private_key \
  vagrant@192.168.57.5:/home/vagrant/orchestrator/output/run_fl_edge1 \
  output/
```

## Edge 2

```bash
scp -r \
  -i .vagrant/machines/master-open5gs-277/libvirt/private_key \
  vagrant@192.168.57.5:/home/vagrant/orchestrator/output/run_fl_edge2 \
  output/
```

Verify:

```bash
du -sh \
  output/run_fl_edge1 \
  output/run_fl_edge2
```

```bash
ls -lh \
  output/run_fl_edge1/pcaps/
```

```bash
ls -lh \
  output/run_fl_edge2/pcaps/
```

---

# 29. Extract the datasets

Dataset extraction is performed on the **host**.

The extraction script transforms raw N3/N4 captures into fixed-duration temporal windows and associates them with the experiment labels.

A 5-second window is used in the experiments.

## Edge 1

```bash
python3 \
  scripts/extract_global_windows.py \
  output/run_fl_edge1 \
  5
```

## Edge 2

```bash
python3 \
  scripts/extract_global_windows.py \
  output/run_fl_edge2 \
  5
```

General syntax:

```text
extract_global_windows.py <run_directory> <window_size_seconds>
```

For each run, the script generates:

```text
dataset_n3_global_w5s.csv
dataset_n4_global_w5s.csv
dataset_crossplane_global_w5s.csv
```

The cross-plane dataset synchronizes N3 and N4 observations **for the same edge/run**. It is therefore generated independently for Edge 1 and Edge 2.

Example:

```text
output/
├── run_fl_edge1/
│   ├── events.csv
│   ├── dataset_n3_global_w5s.csv
│   ├── dataset_n4_global_w5s.csv
│   └── dataset_crossplane_global_w5s.csv
│
└── run_fl_edge2/
    ├── events.csv
    ├── dataset_n3_global_w5s.csv
    ├── dataset_n4_global_w5s.csv
    └── dataset_crossplane_global_w5s.csv
```

The two files named `dataset_crossplane_global_w5s.csv` are different files located in different run directories:

```text
output/run_fl_edge1/dataset_crossplane_global_w5s.csv
output/run_fl_edge2/dataset_crossplane_global_w5s.csv
```

A small number of `unknown` windows may occur around capture/event temporal boundaries.

---

# 30. Final checks

## Kubernetes nodes

From the master:

```bash
kubectl get nodes
```

Expected:

```text
master-open5gs      Ready
worker-open5gs-01   Ready
worker-open5gs-02   Ready
```

## All pods

```bash
kubectl get pods -A
```

## Multus and OVS-CNI

```bash
kubectl get pods \
  -n kube-system \
  | grep -E 'multus|ovs'
```

## Secondary networks

```bash
kubectl get \
  network-attachment-definitions \
  -A
```

## Node labels

```bash
kubectl get nodes \
  --show-labels
```

## Attacker tools

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- which hping3
```

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- which iperf3
```

```bash
kubectl exec \
  pfcp-attacker \
  -n open5gs-edge \
  -- python3 -c \
  "import scapy, faker; print('OK')"
```

## UE tunnels

Retrieve the Edge 1 UE pod again so that the final checks do not depend on a previously defined shell variable:

```bash
UEPOD=$(kubectl get pods \
  -n open5gs-core \
  -l component=ue-lifecycle,edge=edge1 \
  -o jsonpath='{.items[0].metadata.name}')
```

Check the UE tunnels:

```bash
kubectl exec \
  -n open5gs-core \
  "$UEPOD" \
  -- ip -o -4 addr show \
  | grep uesim
```

## HTTP traffic

```bash
kubectl exec \
  -n open5gs-core \
  "$UEPOD" \
  -- grep "code=200" \
  /tmp/ue-traffic.log \
  | tail -3
```

---

# Experiment output validated with Open5GS v2.7.7

The validated 40-minute runs generate:

```text
Edge 1:
  N3 PCAP
  N4 PCAP
  events.csv
  N3 5-second dataset
  N4 5-second dataset
  cross-plane 5-second dataset

Edge 2:
  N3 PCAP
  N4 PCAP
  events.csv
  N3 5-second dataset
  N4 5-second dataset
  cross-plane 5-second dataset
```

The attack exposure is intentionally asymmetric:

```text
Edge 1                           Edge 2
-------------------------------------------------
pfcp_flood                       pfcp_flood
pfcp_session_del                 pfcp_session_mod
gtp_flood                        -
udp_flood_low                    -
udp_flood_medium                 -
-                                udp_flood_high
-                                udp_flood_max
```

This setup allows each site to retain its local observations while training models that can subsequently be evaluated on attack classes absent from a client's local training data.

---

# Version summary

```text
Open5GS        2.7.7
UERANSIM       3.2.6
Kubernetes     1.29.15
Helm chart     gradiantcharts/open5gs 2.3.4
Multus         thick daemonset
OVS-CNI        upstream
OVS VXLAN      UDP/8472
N3             GTP-U / UDP 2152
N4             PFCP / UDP 8805
Dataset window 5 seconds
```
