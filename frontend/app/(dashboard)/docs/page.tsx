import { SwaggerViewer } from "@/components/supervisory/docs/swagger-viewer"

export default function DocsPage() {
  return (
    <div className="space-y-6 font-sans">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-2 border-b border-border/60 gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            API Documentation &amp; Contract Registry
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Interactive OpenAPI 3.1 specification for Orion SAT-SA core backend services
          </p>
        </div>
      </div>
      <SwaggerViewer />
    </div>
  )
}
