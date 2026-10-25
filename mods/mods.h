/* Functions shared between mods (not firmware: see fw.h for those). */
#ifndef MODS_H
#define MODS_H

/* ascent.c */
int double_to_cm(unsigned lo, unsigned hi, int *cm);
unsigned long long cm_to_double(unsigned cm);
#define TRIP_ASCENT(t)   (((unsigned *)(t))[0x28 / 4])
#define TRIP_DESCENT(t)  (((unsigned *)(t))[0x2C / 4])
#define TOTAL_MAX_CM     100000000u    /* 1,000,000 m: anything above is garbage */

/* profile.c */
void profile_feed(int cm);              /* one elevation sample (UI task, ~1 Hz with a fix) */
unsigned udiv(unsigned n, unsigned d);  /* n / d without libgcc */

#endif
