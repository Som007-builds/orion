import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-1 focus:ring-ring font-sans",
  {
    variants: {
      variant: {
        default:
          "border border-primary/20 bg-primary/10 text-primary font-semibold",
        secondary:
          "border border-border bg-secondary text-secondary-foreground",
        destructive:
          "border border-destructive/20 bg-destructive/10 text-destructive font-semibold",
        outline:
          "border border-border text-foreground bg-transparent",
        accent:
          "border border-primary bg-primary text-primary-foreground font-semibold",
        success:
          "border border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-semibold",
        warning:
          "border border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-400 font-semibold",
        muted:
          "border border-border/40 bg-muted text-muted-foreground",
        assessable:
          "border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        partial:
          "border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
        not_assessable:
          "border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 font-semibold",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
