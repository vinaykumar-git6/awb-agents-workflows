// Connects vnet-ek (app/ACA side) to vnet-hub (private endpoints + DNS) so the
// privately-deployed splitter can resolve and reach the Service Bus and Blob
// private endpoints.
//
// Creates:
//   1. VNet peering vnet-ek  -> vnet-hub   (in emirates-ai-usecase)
//   2. VNet peering vnet-hub -> vnet-ek    (in azure-vk-hub, via module)
//   3. Private DNS zone links for vnet-ek on:
//        - privatelink.servicebus.windows.net
//        - privatelink.blob.core.windows.net
//
// Deploy to the emirates-ai-usecase resource group.

targetScope = 'resourceGroup'

@description('Spoke VNet (app/ACA side) name.')
param spokeVnetName string = 'vnet-ek'

@description('Hub VNet name (private endpoints + DNS).')
param hubVnetName string = 'vnet-hub'

@description('Resource group that holds the hub VNet and private DNS zones.')
param hubResourceGroup string = 'azure-vk-hub'

@description('Tags.')
param tags object = {
  workload: 'skycargo-awb'
  component: 'network-peering'
}

var dnsZoneServiceBus = 'privatelink.servicebus.windows.net'
#disable-next-line no-hardcoded-env-urls
var dnsZoneBlob = 'privatelink.blob.core.windows.net'

resource spokeVnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: spokeVnetName
}

resource hubVnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: hubVnetName
  scope: resourceGroup(hubResourceGroup)
}

// 1. Spoke -> Hub peering (local RG).
resource spokeToHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-09-01' = {
  parent: spokeVnet
  name: 'peer-to-${hubVnetName}'
  properties: {
    remoteVirtualNetwork: {
      id: hubVnet.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

// 2 + 3. Hub -> Spoke peering and DNS links live in the hub RG.
module hubSide 'hub-side.bicep' = {
  name: 'hub-side-peering-dns'
  scope: resourceGroup(hubResourceGroup)
  params: {
    hubVnetName: hubVnetName
    spokeVnetName: spokeVnetName
    spokeVnetId: spokeVnet.id
    dnsZoneServiceBus: dnsZoneServiceBus
    dnsZoneBlob: dnsZoneBlob
    tags: tags
  }
}

output spokeToHubPeeringId string = spokeToHub.id
