# Source-library filters and compact cards

Sources offers a Subjects per source selector: All sources, No subjects, One
subject, Multiple subjects. It replaces the old multiple-subject checkbox and
works with existing source-ID/URL search, website filter and sorting. The
subject_scope query parameter persists in URLs and pagination; old
multiple_subjects=true links still work. Invalid or conflicting filter values
return 422. Counts use current unmerged subjects, including zero-subject sources.

Source IDs use a smaller, single-line font. Cards maintain a minimum useful width;
View Source and Open Source use equal halves and 36px heights. Text scales with
the card width to avoid wrapping on narrow screens.

Validation: 24 console unit checks, five targeted database checks for source
filters/pagination/zero-subject registration, and browser layout checks at
320/390/601/768/1280px. No changes to processing jobs.
