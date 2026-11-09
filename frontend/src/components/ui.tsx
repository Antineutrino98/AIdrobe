/** U2 基础组件：Radix Dialog + 自定义 Tailwind 控件（shadcn 风格的轻量实现）。 */
import * as DialogPrimitive from '@radix-ui/react-dialog'
import clsx, { type ClassValue } from 'clsx'
import { X } from 'lucide-react'
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ---------------------------------------------------------------- Button
type ButtonVariant = 'primary' | 'outline' | 'ghost' | 'soft' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

export function Button({
  variant = 'outline',
  size = 'md',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
}) {
  return (
    <button
      type="button"
      className={cn(
        'ui-btn',
        `ui-btn-${variant}`,
        size === 'sm' && 'ui-btn-sm',
        size === 'lg' && 'ui-btn-lg',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------- Fields
export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('ui-field', className)} {...props} />
}

export function Select({
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn('ui-field', className)} {...props}>
      {children}
    </select>
  )
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('ui-field', className)} {...props} />
}

// ---------------------------------------------------------------- Badge
export function Badge({
  tone = 'neutral',
  className,
  children,
  ...props
}: {
  tone?: 'neutral' | 'sage' | 'clay' | 'rose' | 'amber'
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'ui-badge',
        tone !== 'neutral' && `ui-badge-${tone}`,
        className,
      )}
      {...props}
    >
      {children}
    </span>
  )
}

// ---------------------------------------------------------------- Skeleton
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} />
}

// ---------------------------------------------------------------- Dialog
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  width = 'max-w-lg',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  width?: string
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[110] bg-[#2d2a26]/45 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-[111] max-h-[88vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2',
            'overflow-y-auto rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-[0_20px_60px_rgba(45,42,38,0.25)]',
            width,
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="t-title text-[var(--ink)]">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="t-meta mt-1">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close
              className="shrink-0 rounded-lg p-1.5 text-[var(--ink-3)] transition hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
              aria-label="关闭"
            >
              <X size={18} />
            </DialogPrimitive.Close>
          </div>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

// ---------------------------------------------------------------- Segmented
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  className?: string
}) {
  return (
    <div
      className={cn(
        'inline-flex flex-wrap gap-1 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-1',
        className,
      )}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-lg px-3 py-1.5 transition',
            value === option.value
              ? 'bg-[var(--sage-deep)] font-medium text-white'
              : 'text-[var(--ink-2)] hover:bg-[var(--surface-2)]',
          )}
          style={{ fontSize: 12 }}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
