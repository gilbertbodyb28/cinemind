import { forwardRef } from "react";
import { cn } from "@/lib/utils";

const Card = forwardRef(({ className, interactive = false, ...props }, ref) => (
  <section
    ref={ref}
    className={cn("glass rounded-2xl", interactive && "poster-hover", className)}
    {...props}
  />
));
Card.displayName = "Card";

const CardHeader = forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-5 pb-0 sm:p-6 sm:pb-0", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

const CardContent = forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-5 sm:p-6", className)} {...props} />
));
CardContent.displayName = "CardContent";

export { Card, CardHeader, CardContent };
