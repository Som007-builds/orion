declare module "swagger-ui-react" {
  import * as React from "react"

  export interface SwaggerUIProps {
    url?: string
    spec?: Record<string, unknown>
    layout?: string
    docExpansion?: "list" | "full" | "none"
    defaultModelExpandDepth?: number
    defaultModelsExpandDepth?: number
    filter?: boolean | string
  }

  const SwaggerUI: React.ComponentType<SwaggerUIProps>
  export default SwaggerUI
}
