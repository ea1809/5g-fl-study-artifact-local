IMAGE_NAME = "peru/ubuntu-20.04-server-amd64"

MASTER_NODE_IP = "192.168.57.5"
MASTER_SSH_FORWARDED_PORT = 2822
WORKER_NODE_IPS = ["192.168.57.6", "192.168.57.7"]

Vagrant.configure(2) do |config|
    config.vm.box = IMAGE_NAME
    config.vm.box_check_update = false

    config.vm.provider "libvirt" do |libvirt|
        libvirt.default_prefix = ""
    end

    config.vm.network :forwarded_port,
        guest: 22,
        host: 2222,
        id: "ssh",
        disabled: true

    config.vm.define "master-open5gs-277" do |master|
        master.vm.provider "libvirt" do |v|
            v.memory = 4096
            v.cpus = 4
        end

        master.vm.hostname = "master-open5gs"
        master.vm.network "private_network", ip: MASTER_NODE_IP
        master.vm.network "forwarded_port",
            guest: 22,
            host: MASTER_SSH_FORWARDED_PORT,
            auto_correct: true
        master.vm.network "forwarded_port",
            guest: 6443,
            host: 7443,
            auto_correct: true
    end

    WORKER_NODE_IPS.each_with_index do |node_ip, index|
        machine_name = "worker-open5gs-277-#{'%02d' % (index + 1)}"
        hostname = "worker-open5gs-#{'%02d' % (index + 1)}"
        forwarded_port = MASTER_SSH_FORWARDED_PORT + index + 1

        config.vm.define machine_name do |worker|
            worker.vm.provider "libvirt" do |v|
                v.memory = 8192
                v.cpus = 4
            end

            worker.vm.hostname = hostname
            worker.vm.network "private_network", ip: node_ip
            worker.vm.network "forwarded_port",
                guest: 22,
                host: forwarded_port,
                auto_correct: true
        end
    end
end
