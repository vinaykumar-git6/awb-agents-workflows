using './main.bicep'

param location = 'uaenorth'

param environmentName = 'cae-skycargo-internal'

param acrName = 'acrvk012826'
param acrResourceGroup = 'azure-vk-rg'
param containerImage = 'acrvk012826.azurecr.io/awb-db-updater:v1'

param appName = 'awb-db-updater'

param serviceBusNamespaceName = 'awb-sb-ek'
param queueName = 'async-db-update-q'

param pgHost = 'devpostgresvinay.postgres.database.azure.com'
param pgDatabase = 'postgres'
param pgUser = 'awb-db-updater'

param targetPort = 8000

// AcrPull already assigned on first deploy — skip to keep re-runs idempotent.
param assignAcrPull = false

param tags = {
  workload: 'skycargo-awb'
  component: 'awb-db-updater'
  environment: 'dev'
}
