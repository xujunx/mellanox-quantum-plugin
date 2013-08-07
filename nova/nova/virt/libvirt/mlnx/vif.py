# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 Mellanox Technologies, Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
from nova import exception
#from nova.openstack.common import cfg
from oslo.config import cfg
from nova.network import model as network_model
from nova.openstack.common import log as logging
from nova import utils
from nova.virt.libvirt import vif
#from nova.virt.libvirt.mlnx import conn_utils 
from nova.virt.libvirt.mlnx import config  as mlxconfig


mlnx_vif_opts = [
    cfg.StrOpt('fabric',
                default='default',
                help='Physical Network Name'),
]

CONF = cfg.CONF
CONF.register_opts(mlnx_vif_opts)

LOG = logging.getLogger(__name__)
HEX_BASE = 16

VIF_TYPE_DIRECT = 'direct'
VIF_TYPE_HOSTDEV = 'hostdev'
SUPPORTED_VIF_TYPES = (VIF_TYPE_DIRECT, VIF_TYPE_HOSTDEV,
                       network_model.VIF_TYPE_BRIDGE)

class MlxEthVIFDriver(vif.LibvirtBaseVIFDriver):
    """VIF driver for Mellanox Embedded switch Plugin"""
    def __init__(self,get_connection):
        super(MlxEthVIFDriver, self).__init__(get_connection)
        self.fabric =  CONF.fabric
        self.libvirt_gen_drv = vif.LibvirtGenericVIFDriver(get_connection)

    def get_dev_config(self, mac_address, dev):
        conf = None
        if self.vnic_type == VIF_TYPE_DIRECT:
            conf = mlxconfig.MlxLibvirtConfigGuestInterface()
            conf.source_dev = dev
            conf.mac_addr = mac_address
        elif self.vnic_type == VIF_TYPE_HOSTDEV:
            conf = mlxconfig.MlxLibvirtConfigGuestDevice()
            self._set_source_address(conf , dev)
        return conf
    
    def get_config(self, instance, network, mapping, image_meta):
        vif_type = mapping.get('vif_type')
        LOG.debug(_("vif_type=%(vif_type)s instance=%(instance)s "
                    "network=%(network)s mapping=%(mapping)s")
                  % locals())
 
        self._check_vif_type(vif_type)
        self.vnic_type = vif_type    
        vnic_mac = mapping['mac']
        device_id = instance['uuid']
        try:
            if vif_type == VIF_TYPE_HOSTDEV:
                dev_name = None
                try:
                    res = utils.execute('ebrctl', 'allocate-port', vnic_mac, device_id, self.fabric, vif_type)
                    dev = res[0].strip()

                except exception.ProcessExecutionError:
                    LOG.exception(_("Failed while config vif"), instance=instance)
                    dev = None
                    
            elif vif_type == VIF_TYPE_DIRECT:
                dev = mapping['vif_devname'].replace('tap','eth')
            else:
                conf = self.libvirt_gen_drv.get_config(instance, network, mapping, image_meta)
                return conf
        except Exception as e:
            LOG.debug("Error in get_config: %s", e) 
            raise exception.NovaException(_("Processing Failure during  vNIC allocation"))
        #Allocation Failed
        if dev is None:
            raise exception.NovaException(_("Failed to allocate device for vNIC"))
        conf = self.get_dev_config(vnic_mac, dev)
        return conf
 
    def plug(self, instance, vif):
        network, mapping = vif
        vnic_mac = mapping['mac']
        device_id = instance['uuid']
        vif_type = mapping.get('vif_type')
        dev_name = None

        self._check_vif_type(vif_type)

        try:
            if vif_type == network_model.VIF_TYPE_BRIDGE:
                self.libvirt_gen_drv.plug(instance, vif)
            else:            
                if vif_type == VIF_TYPE_DIRECT:
                    dev_name = mapping['vif_devname'].replace('tap','eth')
                dev = utils.execute('ebrctl', 'add-port', vnic_mac, device_id,
                                    self.fabric, vif_type, dev_name)
                if dev is None:
                    error_msg = "Cannot plug VIF with no allocated device "
                    raise exception.NovaException(_(error_msg))

        except Exception as e:
            LOG.debug(_("Error in Plug: %s"), e) 
            raise exception.NovaException(_("Processing Failure during vNIC plug"))
 
    def unplug(self, instance, vif):
        network, mapping = vif
        vnic_mac = mapping['mac']
        vif_type = mapping.get('vif_type')

        self._check_vif_type(vif_type)

        try:
            if vif_type in (VIF_TYPE_DIRECT, VIF_TYPE_HOSTDEV):
                utils.execute('ebrctl', 'del-port', self.fabric, vnic_mac)
            else:
                self.libvirt_gen_drv.unplug(instance, vif)
        except Exception,e:
            LOG.warning(_("Failed while unplugging vif %s"), e)

    def _str_to_hex(self,str_val):
        ret_val = hex(int(str_val,HEX_BASE))
        return ret_val
    
    def _set_source_address(self, conf , dev):
        source_address = re.split(r"\.|\:", dev)
        conf.domain, conf.bus , conf.slot, conf.function = source_address
        conf.domain = self._str_to_hex(conf.domain)
        conf.bus = self._str_to_hex(conf.bus)
        conf.slot = self._str_to_hex(conf.slot)
        conf.function = self._str_to_hex(conf.function)
            
    def _check_vif_type(self, vif_type):
        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))

        if vif_type not in SUPPORTED_VIF_TYPES:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)

