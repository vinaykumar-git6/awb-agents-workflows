using './main.bicep'

param location = 'uaenorth'

// Existing VNet to inject the Container Apps environment into.
param vnetName = 'vnet-ek'
param acaSubnetName = 'snet-aca-infra'
param acaSubnetPrefix = '10.10.4.0/23'

// Container registry + image (existing ACR in azure-vk-rg).
param acrName = 'acrvk012826'
param acrResourceGroup = 'azure-vk-rg'
param containerImage = 'acrvk012826.azurecr.io/awb-pdf-splitter:v1'

param appName = 'awb-pdf-splitter'
param environmentName = 'cae-skycargo-internal'
param logAnalyticsName = 'log-skycargo-aca'

// Data + messaging targets.
param storageAccountName = 'awbstorageek'
param blobContainerName = 'awb-input'
param blobPathTemplate = '{document_name}/{date}/{flight}/{awb}.pdf'
param serviceBusNamespaceName = 'awb-sb-ek'

param targetPort = 8000

param tags = {
  workload: 'skycargo-awb'
  component: 'aws-splitter-aca'
  environment: 'dev'
}
