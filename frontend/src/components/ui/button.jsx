import { forwardRef } from "react";
import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";

const variants = cva("inline-flex items-center justify-center gap-2 transition-colors", {
  variants: {
    variant: {
      primary: "glass-strong px-5 py-2.5 rounded-full text-sm font-medium hover:brutal-shadow-rose",
      secondary: "chip hover:chip-rose",
      ghost: "chip hover:chip-rose",
      icon: "w-10 h-10 rounded-full glass grid place-items-center",
      danger: "chip hover:chip-rose text-rose-400",
    },
    size: {
      default: "",
      sm: "text-xs",
      lg: "px-6 py-3",
    },
  },
  defaultVariants: {
    variant: "primary",
    size: "default",
  },
});

const Button = forwardRef(
  ({ className, variant, size, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(variants({ variant, size }), className)}
      {...props}
    />
  ),
);

Button.displayName = "Button";

export { Button, variants as buttonVariants };
