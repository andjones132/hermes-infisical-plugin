The goal is to build a plug-in for Hermes Agent from Nous Research per their specification here:
https://hermes-agent.nousresearch.com/docs/developer-guide/secret-source-plugin

May need to research exactly how such plug-ins are built.

This will be a plugin that will allow reading values from Infisical secret store. It should support
 - Providing the URL of the infisical store.
 - Authenticating to the store via Infisical Agent running locally (it may need to know where the agent stores token).
 - Authenticating to the store via Machine Identity.
 - Being able to retrieve secrets from the store given an Organization name, Project name, Folder name and then Key name.
 
 Should there be any other useful features as part of this plug-in?
 - Ability to modify/save values?
 - Ability to list available values?
 - Maybe dynamic secrets?
