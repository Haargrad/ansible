from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import argparse
import sys

# Disable SSL certificate verification (may be insecure in production environments)
context = ssl._create_default_https_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

# Parse command-line arguments
parser = argparse.ArgumentParser(description='VMware SCSI to NVMe migration script.')
parser.add_argument('--vm_name', required=True, help='Name of the virtual machine')
parser.add_argument('--user', required=True, help='vCenter username')
parser.add_argument('--password', required=True, help='vCenter password')
parser.add_argument('--vmware_host', required=True, help='vCenter server hostname or IP address')

args = parser.parse_args()

# Connect to the vCenter server
si = SmartConnect(
    host=args.vmware_host,
    user=args.user,
    pwd=args.password,
    sslContext=context
)

def get_vm_by_name(name):
    content = si.RetrieveContent()
    for datacenter in content.rootFolder.childEntity:
        for cluster in datacenter.hostFolder.childEntity:
            for host in cluster.host:
                for vm in host.vm:
                    if vm.name == name:
                        return vm
    return None

# Retrieve the VM
vm = get_vm_by_name(args.vm_name)
if vm is None:
    print("VM not found")
    Disconnect(si)
    sys.exit()

# Create a new NVMe controller
controller = vim.VirtualNVMEController()
controller.key = -101
controller.busNumber = 0

# Create VM configuration specification
spec = vim.VirtualMachineConfigSpec()
device_spec = vim.VirtualDeviceConfigSpec()
device_spec.operation = vim.VirtualDeviceConfigSpec.Operation.add
device_spec.device = controller
spec.deviceChange = [device_spec]

# Reconfigure the VM to add the NVMe controller
task = vm.ReconfigVM_Task(spec)
while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
    pass
if task.info.state == vim.TaskInfo.State.error:
    print(f"Error: {task.info.error}")
    Disconnect(si)
    sys.exit()

# Retrieve all NVMe controllers from the VM
nvme_controllers = [device for device in vm.config.hardware.device if isinstance(device, vim.VirtualNVMEController)]

if not nvme_controllers:
    print("No NVMe controllers found")
    Disconnect(si)
    sys.exit()

nvme_controller = nvme_controllers[0]
nvme_controller_key = nvme_controller.key

# Retrieve all virtual disks from the VM
disks = [device for device in vm.config.hardware.device if isinstance(device, vim.VirtualDisk)]

device_changes = []
for disk in disks:
    device_spec = vim.VirtualDeviceConfigSpec()
    device_spec.operation = vim.VirtualDeviceConfigSpec.Operation.edit
    device_spec.device = disk
    device_spec.device.controllerKey = nvme_controller_key
    device_changes.append(device_spec)

# Create a new VM configuration specification to update disks
spec = vim.VirtualMachineConfigSpec()
spec.deviceChange = device_changes

# Reconfigure the VM to update virtual disks with the NVMe controller
task = vm.ReconfigVM_Task(spec)
while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
    pass
if task.info.state == vim.TaskInfo.State.error:
    print(f"Error: {task.info.error}")
    Disconnect(si)
    sys.exit()

# Retrieve all devices from the VM
devices = vm.config.hardware.device

# Find all SCSI controllers
scsi_controllers = [device for device in devices if isinstance(device, vim.VirtualSCSIController)]

# Find all SCSI disks associated with the SCSI controllers
scsi_disks = [device for device in devices if isinstance(device, vim.VirtualDisk) and device.controllerKey in [controller.key for controller in scsi_controllers]]

device_changes = []
# Remove all SCSI disks
for disk in scsi_disks:
    device_spec = vim.VirtualDeviceConfigSpec()
    device_spec.operation = vim.VirtualDeviceConfigSpec.Operation.remove
    device_spec.device = disk
    device_changes.append(device_spec)

# Remove all SCSI controllers
for controller in scsi_controllers:
    device_spec = vim.VirtualDeviceConfigSpec()
    device_spec.operation = vim.VirtualDeviceConfigSpec.Operation.remove
    device_spec.device = controller
    device_changes.append(device_spec)

# Create a new VM configuration specification for device removal
spec = vim.VirtualMachineConfigSpec()
spec.deviceChange = device_changes

# Reconfigure the VM to remove SCSI devices
task = vm.ReconfigVM_Task(spec)
while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
    pass
if task.info.state == vim.TaskInfo.State.error:
    print(f"Error: {task.info.error}")

# Disconnect from the server
Disconnect(si)
