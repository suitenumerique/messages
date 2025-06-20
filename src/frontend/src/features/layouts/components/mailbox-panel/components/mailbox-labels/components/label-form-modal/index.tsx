import { Label, TreeLabel } from "@/features/api/gen";
import { useLabelsCreate, useLabelsList, useLabelsUpdate } from "@/features/api/gen/labels/labels";
import { RhfInput, RhfSelect } from "@/features/forms/components/react-hook-form";
import { useMailboxContext } from "@/features/providers/mailbox";
import { ColorHelper } from "@/features/utils/color-helper";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@openfun/cunningham-react";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";

type LabelModalProps = {
    isOpen: boolean;
    onClose: () => void;
    label?: TreeLabel
}

const formSchema = z.object({
    name: z.string().min(1, { message: 'labels.form.errors.name_required' }),
    color: z.string().regex(/^#([0-9a-fA-F]{6})$/),
    parent_label: z.string().optional(),
});

type FormFields = z.infer<typeof formSchema>;

/**
 * Modal component which contains a form to create/update a label
 */
export const LabelModal = ({ isOpen, onClose, label }: LabelModalProps) => {
    const { t } = useTranslation();
    const form = useForm({
        resolver: zodResolver(formSchema),
        defaultValues: {
            name: label?.display_name ?? '',
            color: label?.color ?? '#E3E3FD',
            parent_label: label?.name.split('/').slice(0, -1).join('/') ?? undefined,
        },
    });
    const charColor = useMemo(
      () => ColorHelper.getContrastColor(form.watch('color')),
      [form.watch('color')]
  );

    const createMutation = useLabelsCreate();
    const updateMutation = useLabelsUpdate();
    const { selectedMailbox } = useMailboxContext();
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const queryClient = useQueryClient();
    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox!.id })
    const flattenLabels = useMemo(() => {
      if (!labelsQuery.data) return [];

      type TreeLabelOption = {
        label: string;
        value: string;
        level: number;
      }

      const flatten = (labels: readonly TreeLabel[], level = 0): TreeLabelOption[] => {
        return labels.map((label) => (
          [
            { label: label.display_name, value: label.name, level },
            ...flatten(label.children, level + 1)
          ]
        )).flat();
      }

      return flatten((labelsQuery.data.data)).filter(
        // Do not display current label and its children as options to nest the current label
        (option) => !label || !option.value.startsWith(label.name)
      );
    }, [labelsQuery.data]);

    const handleClose = () => {
      form.reset();
      onClose();
    }

    const handleSubmit = (data: FormFields) => {
      const mutation = label ? updateMutation : createMutation;

      mutation.mutate({
        id: label?.id || '',
        data: {
          name: data.parent_label ? `${data.parent_label}/${data.name}` : data.name,
          color: data.color,
          mailbox: selectedMailbox!.id,
        }
      }, {
        onSuccess: (data) => {
          queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
          // If the active label has been updated, update the search params
          if (searchParams.get('label_slug') === label?.slug) {
            const newSearchParams = new URLSearchParams(searchParams.toString());
            newSearchParams.set('label_slug', (data.data as Label).slug);
            router.push(`${pathname}?${newSearchParams.toString()}`);
          }
          handleClose();
        }
      });
    }

    return (
      <Modal
        size={ModalSize.SMALL}
        isOpen={isOpen}
        onClose={handleClose}
        title={label ? t('labels.update') : t('labels.create')}
        closeOnClickOutside
      >
        <FormProvider {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="label-form">
            <div className="form-field-row">
              <RhfInput
                name="name"
                label={t('labels.form.fields.name')}
                text={form.formState.errors.name?.message && t(form.formState.errors.name.message)}
              />
              <label
                  className="label-form__color-field"
                  htmlFor="color"
                  style={{ '--char-color': charColor } as React.CSSProperties}
              >
                  <span className="c__offscreen">{t('labels.form.fields.color')}</span>
                  <Icon name="format_color_fill" className="label-form__color-field__icon" size={18} />
                  <input
                    type="color"
                    {...form.register('color')}
                  />
              </label>
            </div>
            <div className="form-field-row">
              <RhfSelect
                name="parent_label"
                label={t('labels.form.fields.parent_label')}
                options={flattenLabels.map((option) => ({
                  label: option.label,
                  value: option.value,
                  render: () => (
                    <div className="label-form__select-option" style={{ paddingLeft: `${option.level * 1.5}rem` }}>
                      <span className="label-form__select-option__value">{option.label}</span>
                    </div>
                  )
                }))}
                searchable
                fullWidth
              />
            </div>
            <footer className="form-field-row">
              <Button type="button" color="secondary" size="medium" onClick={onClose}>
                {t('actions.cancel')}
              </Button>
              <Button type="submit" color="primary" size="medium">
                {label ? t('labels.form.submit_update') : t('labels.form.submit_create')}
              </Button>
            </footer>
          </form>
        </FormProvider>
      </Modal>
    )
  }
