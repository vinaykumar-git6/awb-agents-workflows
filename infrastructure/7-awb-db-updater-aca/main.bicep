// Deploy the awb-db-updater PRIVATELY to Azure Container Apps inside vnet-ek.
//
// Reuses the existing internal (VNet-injected) Container Apps environment
// (cae-skycargo-internal). The app consumes the async-db-update-q queue and
// upserts rows into the `skycargo` schema on the PostgreSQL flexible server,
// authenticating with its SYSTEM-ASSIGNED managed identity (keyless) for both
// Service Bus and PostgreSQL.
//
// RBAC granted to the app identity:
//   * AcrPull on the container registry (cross-RG module)
//   * Azure Service Bus Data Receiver + Sender on the namespace
//
// NOTE: Granting PostgreSQL access cannot be done in ARM/Bicep. After the app
// is deployed, register its managed identity as a PostgreSQL Entra role and
// grant it rights on the skycargo schema (see deploy.ps1 / README).

targetScope = 'resourceGroup'

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing internal Container Apps environment.')
param environmentName string = 'cae-skycargo-internal'

@description('Existing Azure Container Registry name.')
param acrName string

@description('Resource group that holds the Azure Container Registry.')
param acrResourceGroup string = 'azure-vk-rg'

@description('Container image reference, e.g. <acr>.azurecr.io/awb-db-updater:v1.')
param containerImage string

@description('Container App name. Also used as the PostgreSQL role name.')
param appName string = 'awb-db-updater'

@description('Existing Service Bus namespace that holds the DB-update queue.')
param serviceBusNamespaceName string = 'awb-sb-ek'

@description('Service Bus queue the worker consumes.')
param queueName string = 'async-db-update-q'

@description('PostgreSQL flexible server host FQDN.')
param pgHost string = 'devpostgresvinay.postgres.database.azure.com'

@description('PostgreSQL database name.')
param pgDatabase string = 'postgres'

@description('PostgreSQL role name mapped to the app managed identity.')
param pgUser string = 'awb-db-updater'

@description('Container target port.')
param targetPort int = 8000

@description('Set false to skip the AcrPull role assignment if it already exists.')
param assignAcrPull bool = true

@description('Tags applied to resources.')
param tags object = {
  workload: 'skycargo-awb'
  component: 'awb-db-updater'
}

var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var roleServiceBusDataReceiver = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
var roleServiceBusDataSender = '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'

// ---------------------------------------------------------------------------
// Existing resources
// ---------------------------------------------------------------------------

resource acaEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
  scope: resourceGroup(acrResourceGroup)
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

// Queue that carries DB-update messages (stage/state metadata) to this worker.
resource dbUpdateQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: queueName
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    enablePartitioning: false
  }
}

// ---------------------------------------------------------------------------
// Container App (no ingress — background queue worker)
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
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'SERVICEBUS_NAMESPACE'
              value: '${serviceBusNamespaceName}.servicebus.windows.net'
            }
            {
              name: 'SERVICEBUS_QUEUE'
              value: queueName
            }
            {
              name: 'PGHOST'
              value: pgHost
            }
            {
              name: 'PGDATABASE'
              value: pgDatabase
            }
            {
              name: 'PGUSER'
              value: pgUser
            }
            {
              name: 'PGPORT'
              value: '5432'
            }
            {
              name: 'PGSSLMODE'
              value: 'require'
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

module raAcrPull 'acr-pull.bicep' = if (assignAcrPull) {
  name: 'dbupd-acrpull'
  scope: resourceGroup(acrResourceGroup)
  params: {
    acrName: acrName
    principalId: containerApp.identity.principalId
    roleAcrPull: roleAcrPull
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

// Sender is required because the consumer reschedules messages (backoff).
resource raSbSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, containerApp.id, roleServiceBusDataSender)
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleServiceBusDataSender)
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output containerAppId string = containerApp.id
output appPrincipalId string = containerApp.identity.principalId
