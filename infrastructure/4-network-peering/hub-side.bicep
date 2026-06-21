// Hub-side resources for connecting the spoke VNet: the reverse peering and the
// private DNS zone links. Deployed into the hub resource group (azure-vk-hub)
// from the parent module.

targetScope = 'resourceGroup'

@description('Hub VNet name.')
param hubVnetName string

@description('Spoke VNet name (used for naming).')
param spokeVnetName string

@description('Spoke VNet resource id.')
param spokeVnetId string

@description('Service Bus private DNS zone name.')
param dnsZoneServiceBus string

@description('Blob private DNS zone name.')
param dnsZoneBlob string

@description('Tags.')
param tags object

resource hubVnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: hubVnetName
}

resource sbZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: dnsZoneServiceBus
}

resource blobZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: dnsZoneBlob
}

// Reverse peering: Hub -> Spoke.
resource hubToSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-09-01' = {
  parent: hubVnet
  name: 'peer-to-${spokeVnetName}'
  properties: {
    remoteVirtualNetwork: {
      id: spokeVnetId
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

// Link the spoke VNet to the Service Bus private DNS zone.
resource sbZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: sbZone
  name: 'link-${spokeVnetName}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: spokeVnetId
    }
  }
}

// Link the spoke VNet to the Blob private DNS zone.
resource blobZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobZone
  name: 'link-${spokeVnetName}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: spokeVnetId
    }
  }
}

output hubToSpokePeeringId string = hubToSpoke.id
output sbZoneLinkId string = sbZoneLink.id
output blobZoneLinkId string = blobZoneLink.id
