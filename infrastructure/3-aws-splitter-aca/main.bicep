// Deploy the awb-pdf-splitter PRIVATELY to Azure Container Apps inside vnet-ek.
//
// - VNet-injected, INTERNAL-ONLY Container Apps environment (no public ingress).
// - The app is reachable only on its internal FQDN from inside vnet-ek
//   (or peered networks).
// - System-assigned managed identity with:
//     * AcrPull on the container registry
//     * Storage Blob Data Contributor on the data storage account (keyless writes)
//     * Azure Service Bus Data Receiver on the namespace (consume aws-splitter-q)
//
// The container image must be built into ACR BEFORE this template is deployed
// (see deploy.ps1, which runs `az acr build` first and passes containerImage).

targetScope = 'resourceGroup'

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing VNet that the Container Apps environment is injected into.')
param vnetName string = 'vnet-ek'

@description('Name of the dedicated subnet for the Container Apps environment.')
param acaSubnetName string = 'snet-aca-infra'

@description('Address prefix for the Container Apps infrastructure subnet (min /27).')
param acaSubnetPrefix string = '10.10.4.0/23'

@description('Existing Azure Container Registry name (image is built here first).')
param acrName string

@description('Resource group that holds the Azure Container Registry.')
param acrResourceGroup string = resourceGroup().name

@description('Container image reference, e.g. <acr>.azurecr.io/awb-pdf-splitter:v1.')
param containerImage string

@description('Container App name.')
param appName string = 'awb-pdf-splitter'

@description('Container Apps environment name.')
param environmentName string = 'cae-skycargo-internal'

@description('Log Analytics workspace name for the environment.')
param logAnalyticsName string = 'log-skycargo-aca'

@description('Existing data storage account for split AWB output.')
param storageAccountName string = 'awbstorageek'

@description('Destination blob container.')
param blobContainerName string = 'awb-input'

@description('Blob virtual path template for split output.')
param blobPathTemplate string = '{document_name}/{date}/{flight}/{awb}.pdf'

@description('Existing Service Bus namespace that holds the splitter queue.')
param serviceBusNamespaceName string = 'awb-sb-ek'

@description('Container target port.')
param targetPort int = 8000

@description('Tags applied to resources.')
param tags object = {
  workload: 'skycargo-awb'
  component: 'aws-splitter-aca'
}

var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleStorageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var roleServiceBusDataReceiver = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'

// ---------------------------------------------------------------------------
// Existing resources
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' existing = {
  name: vnetName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
  scope: resourceGroup(acrResourceGroup)
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

// ---------------------------------------------------------------------------
// Dedicated subnet for the Container Apps environment
// ---------------------------------------------------------------------------

resource acaSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-09-01' = {
  parent: vnet
  name: acaSubnetName
  properties: {
    addressPrefix: acaSubnetPrefix
    delegations: [
      {
        name: 'aca-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Log Analytics workspace (required by the ACA environment)
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Internal (private) Container Apps environment, VNet-injected
// ---------------------------------------------------------------------------

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnet.id
      // No public ingress: environment is internal-only.
      internal: true
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Container App (internal ingress only)
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          resources: {
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: [
            {
              name: 'BLOB_ACCOUNT_URL'
              value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}'
            }
            {
              name: 'BLOB_CONTAINER'
              value: blobContainerName
            }
            {
              name: 'BLOB_PATH_TEMPLATE'
              value: blobPathTemplate
            }
            {
              name: 'SERVICEBUS_NAMESPACE'
              value: '${serviceBusNamespaceName}.servicebus.windows.net'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Role assignments for the app's managed identity
// ---------------------------------------------------------------------------

// AcrPull is granted in the ACR's resource group via a module (cross-RG scope).
module raAcrPull 'acr-pull.bicep' = {
  name: 'aca-acrpull'
  scope: resourceGroup(acrResourceGroup)
  params: {
    acrName: acrName
    principalId: containerApp.identity.principalId
    roleAcrPull: roleAcrPull
  }
}

resource raBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, containerApp.id, roleStorageBlobDataContributor)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributor)
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource raSbReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, containerApp.id, roleServiceBusDataReceiver)
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleServiceBusDataReceiver)
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output environmentId string = acaEnvironment.id
output containerAppId string = containerApp.id
output internalFqdn string = containerApp.properties.configuration.ingress.fqdn
output appPrincipalId string = containerApp.identity.principalId
