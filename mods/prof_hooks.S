@ Timing wrappers for prof.c. Each one: start = prof_enter(slot, r0), call
@ the original with the caller's r0-r3, prof_exit(slot, start), return the
@ original's r0:r1. They push onto the stack before the call, so they only
@ suit functions without stack arguments (all of these take at most 4).
@
@ Call-site wrappers replace a "bl target" in the UI main loop. Entry wrappers
@ replace a function's first instruction with "b wrap_x": the thunk runs that
@ instruction and continues in the original at +4.

    .syntax unified
    .arm
    .section .text.hooks_prof, "ax"

    .macro TIMED name, slot, target
    .global \name
\name:
    push    {r4-r7, lr}
    push    {r0-r3}
    mov     r5, sp
    bic     sp, sp, #7
    mov     r1, r0
    mov     r0, #\slot
    bl      prof_enter
    mov     sp, r5
    mov     r4, r0                  @ start time
    pop     {r0-r3}
    bl      \target
    mov     r6, r0
    mov     r7, r1
    mov     r5, sp
    bic     sp, sp, #7
    mov     r0, #\slot
    mov     r1, r4
    bl      prof_exit
    mov     sp, r5
    mov     r0, r6
    mov     r1, r7
    pop     {r4-r7, pc}
    .endm

    @ UI main loop call sites (0x30085F38, 0x30085F50, 0x30085F6C, 0x30085F98)
    TIMED   wrap_before, 0, fw_ui_before_dispatch
    TIMED   wrap_dispatch, 1, fw_ui_dispatch_event
    TIMED   wrap_push_screen, 2, fw_win_update
    TIMED   wrap_push_popup, 3, fw_win_update

    @ fread(ptr, size, n, f) 0x30187BA4: first instruction "push {r4, lr}"
    TIMED   wrap_fread, 4, fread_thunk
fread_thunk:
    push    {r4, lr}
    b       fw_fread_body

    @ fseek(f, off, whence) 0x30187D38: first instruction "str lr, [sp, #-4]!"
    TIMED   wrap_fseek, 5, fseek_thunk
fseek_thunk:
    str     lr, [sp, #-4]!
    b       fw_fseek_body

@ IRQ sampler. While prof.c's irq_install has pointed the IRQ handler
@ literal at 0xA0000038 here, every IRQ comes in on this path (IRQ mode,
@ lr = interrupted pc + 4, IRQs masked). Count lr - 4 in a 256-byte bin of
@ 0x30080000-0x306DFFFF (else "other"), then jump to the firmware's handler
@ with every register as it was on entry. The condition flags of IRQ mode
@ are scratch: the interrupted code's are in SPSR_irq.
@ PROF_RAM: +0 hist (u16 *), +4 saved handler, +8 samples, +12 other.
    .global prof_irq
prof_irq:
    sub     sp, sp, #4              @ slot for the jump target
    push    {r0-r2}
    ldr     r0, =PROF_RAM
    ldr     r1, [r0, #8]
    add     r1, r1, #1
    str     r1, [r0, #8]
    ldr     r2, [r0]                @ hist
    cmp     r2, #0
    beq     2f
    sub     r1, lr, #4
    sub     r1, r1, #0x30000000
    subs    r1, r1, #0x80000        @ offset from 0x30080000
    bcc     1f
    cmp     r1, #0x660000
    bcs     1f
    lsr     r1, r1, #8
    add     r2, r2, r1, lsl #1
    ldrh    r1, [r2]
    adds    r1, r1, #1
    cmp     r1, #0x10000            @ saturate
    strhne  r1, [r2]
    b       2f
1:  ldr     r1, [r0, #12]
    add     r1, r1, #1
    str     r1, [r0, #12]
2:  ldr     r1, [r0, #4]
    str     r1, [sp, #12]
    pop     {r0-r2}
    pop     {pc}

    .ltorg

@ CPU clock measurement (prof.c sysinfo): r0 iterations of 16 or 48
@ single-cycle adds plus the loop branch. The difference between the two
@ is 32 cycles per iteration whatever the branch costs.
    .section .text.cpuloop, "ax"
    .balign 32
    .global cpu_loop16
cpu_loop16:
1:  .rept 16
    add     r1, r1, #1
    .endr
    subs    r0, r0, #1
    bne     1b
    bx      lr

    .balign 32
    .global cpu_loop48
cpu_loop48:
1:  .rept 48
    add     r1, r1, #1
    .endr
    subs    r0, r0, #1
    bne     1b
    bx      lr

@ Interrupt masking for the clock measurement. Tasks run in user mode
@ (CPSR mode 0x10), where msr can't change the mask bits, so IRQs go through
@ OS20's own calls: SWI #0 sets the caller's I bit, SWI #1 clears it (SWI
@ handler 0x301FC798). Nothing in OS20 masks FIQ; the msr is tried anyway and
@ only works in a privileged mode. Call these from a task only (an SWI in
@ supervisor mode would overwrite lr_svc).
@ cpu_irq_mask(bits) returns the old CPSR; cpu_irq_restore(old) undoes it.
    .section .text.cpuirq, "ax"
    .global cpu_irq_mask
cpu_irq_mask:
    mrs     r1, cpsr
    tst     r0, #0x80
    beq     1f
    svc     #0                      @ OS20 interrupt lock
1:  orr     r2, r1, r0
    msr     cpsr_c, r2              @ FIQ (and IRQ): privileged modes only
    mov     r0, r1
    bx      lr

    .global cpu_irq_restore
cpu_irq_restore:
    msr     cpsr_c, r0              @ privileged modes only
    tst     r0, #0x80
    bne     1f
    svc     #1                      @ IRQs were on: OS20 interrupt unlock
1:  bx      lr

    .global cpu_get_cpsr
cpu_get_cpsr:
    mrs     r0, cpsr
    bx      lr

@ Compass Process task (0x301AA804). Its sensor read result is in r3 (also
@ stored at 0x002B0958); r8 = &state, r5 = 0, r6/r7/sl live; r0-r4, ip and
@ lr are free here (r4 is recomputed before use, lr was saved by the
@ prologue). prof_compass(err) counts the read and returns nonzero to go on.
    .section .text.hooks_compass, "ax"
    .global compass_fail_tramp
compass_fail_tramp:                 @ replaces "mov r3, #5" at 0x301AA8E8
    mov     r0, r3
    bl      compass_count
    cmp     r0, #0
    bne     fw_compass_loop         @ retry at the next sample
    mov     r3, #5                  @ give up: the original exit
    str     r3, [r8]
    add     sp, sp, #0x20
    pop     {r4, r5, r6, r7, r8, sl, pc}

    .global compass_ok_tramp
compass_ok_tramp:                   @ replaces "b 0x301AA858" at 0x301AA91C
    mov     r0, #0
    bl      compass_count
    b       fw_compass_loop

@ prof_compass(err) with sp 8-byte aligned; returns its result in r0
compass_count:
    mov     ip, sp
    bic     sp, sp, #7
    push    {ip, lr}
    bl      prof_compass
    pop     {ip, lr}
    mov     sp, ip
    bx      lr

@ Bus timing (prof.c sysinfo): r1 loads from r0, r0 += r2 after each, so
@ stride 0 rereads one address. Timed with interrupts locked.
    .section .text.busloop, "ax"
    .balign 32
    .global bus_read_loop
bus_read_loop:
1:  ldr     r3, [r0]
    add     r0, r0, r2
    subs    r1, r1, #1
    bne     1b
    bx      lr
