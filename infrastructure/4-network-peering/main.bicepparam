using './main.bicep'

param spokeVnetName = 'vnet-ek'
param hubVnetName = 'vnet-hub'
param hubResourceGroup = 'azure-vk-hub'

param tags = {
  workload: 'skycargo-awb'
  component: 'network-peering'
  environment: 'dev'
}
