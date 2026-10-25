@ Black box hooks (bbox.c).

    .syntax unified
    .arm
    .section .text.hooks_bbox, "ax"

@ Shutdown routine 0x3008423C (r0 = 0 power off, 1 reboot). Its first
@ instruction "push {r4-r8, lr}" is now "b shutdown_tramp".
    .global shutdown_tramp
shutdown_tramp:
    push    {r0-r3, r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    mov     r1, lr                  @ the caller
    bl      bbox_shutdown
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    push    {r4-r8, lr}
    b       fw_shutdown_body

@ Immediate watchdog reset 0x301A869C (used by reboot paths). First
@ instruction "push {r4, lr}" is now "b wdreset_tramp".
    .global wdreset_tramp
wdreset_tramp:
    push    {r0-r3, r4, lr}
    mov     r4, sp
    bic     sp, sp, #7
    mov     r1, lr
    bl      bbox_reset
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    push    {r4, lr}
    b       fw_wdreset_body

@ Watchdog task: "bl 0x301A864C" at 0x301A88FC forces the reset when a slot
@ has expired; r4 points at that slot. Now "bl wd_expire_tramp": record,
@ then tail-call the original with r0 = 0 and lr = 0x301A8900 as before.
    .global wd_expire_tramp
wd_expire_tramp:
    push    {r0-r3, r4, lr}
    mov     r0, r4
    mov     r4, sp
    bic     sp, sp, #7
    bl      bbox_watchdog
    mov     sp, r4
    pop     {r0-r3, r4, lr}
    b       fw_wd_force_reset

@ Exception handlers, installed by bbox.c in the RAM vector literals
@ (0xA0000024 / 2C / 30) only where those held the stock self-loop. The CPU
@ is in undefined/abort mode with IRQs off; nothing returns from here.
@ Save the registers into the bb_exc record at BBOX_RAM, then loop like
@ stock firmware did, so the hardware watchdog resets the unit.
    .global bbox_exc_undef, bbox_exc_pabort, bbox_exc_dabort
bbox_exc_undef:
    ldr     sp, =BBOX_RAM + 8
    stmia   sp, {r0-r12}
    mov     r0, #1
    b       exc_common
bbox_exc_pabort:
    ldr     sp, =BBOX_RAM + 8
    stmia   sp, {r0-r12}
    mov     r0, #2
    b       exc_common
bbox_exc_dabort:
    ldr     sp, =BBOX_RAM + 8
    stmia   sp, {r0-r12}
    mov     r0, #3
exc_common:
    ldr     r1, =BBOX_RAM
    str     r0, [r1, #0x04]         @ type
    str     lr, [r1, #0x3C]         @ exception-mode lr
    mrs     r2, spsr
    str     r2, [r1, #0x40]
    mrc     p15, 0, r3, c5, c0, 0   @ fault status
    str     r3, [r1, #0x4C]
    mrc     p15, 0, r3, c6, c0, 0   @ fault address
    str     r3, [r1, #0x50]
    @ sp/lr of the interrupted mode (user -> system mode, same registers)
    and     r4, r2, #0x1F
    cmp     r4, #0x10
    moveq   r4, #0x1F
    mrs     r5, cpsr
    bic     r6, r5, #0x1F
    orr     r6, r6, r4
    orr     r6, r6, #0xC0
    msr     cpsr_c, r6
    mov     r7, sp
    mov     r8, lr
    msr     cpsr_c, r5
    str     r7, [r1, #0x44]
    str     r8, [r1, #0x48]
    @ 16 words at that sp, if it points into SDRAM
    add     r10, r1, #0x54
    mov     r9, #0
    tst     r7, #3
    bne     2f
    cmp     r7, #0x40
    blo     2f
    ldr     r3, =0x007FFFC0
    cmp     r7, r3
    bhi     2f
1:  ldr     r3, [r7, r9, lsl #2]
    str     r3, [r10, r9, lsl #2]
    add     r9, r9, #1
    cmp     r9, #16
    bne     1b
2:  ldr     r0, =0x45584321         @ EXC_MAGIC: the record is complete
    str     r0, [r1]
3:  b       3b

    .ltorg
